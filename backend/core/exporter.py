"""
Export pipeline.

Cutting method
--------------
The source is opened ONCE and decoded straight through. Frames and audio
blocks that fall outside the kept segments are dropped inside the filter graph
(select / aselect), then timestamps are rebuilt from scratch. That gives:

- frame-accurate video cuts (no dependence on where keyframes fall)
- audio cuts planned on a 5 ms grid against the running video length, so the
  audio can never drift away from the picture, however many cuts there are
- clean, gap-free timestamps, so no drift-correction resampling is needed

(The previous approach listed the source hundreds of times in a concat file
with inpoint/outpoint, which is only exact for intra-frame codecs.)
"""
import os
import re
import json
import uuid
import threading
import subprocess
from fractions import Fraction
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from backend.config import FFMPEG_PATH, FFPROBE_PATH, OUTPUT_DIR, HAS_NVENC
from backend.core.video_reframer import build_reframe_filter
from backend.core.branding_engine import build_branding_filter
from backend.core.subtitle_generator import generate_subtitles
from backend.core.audio_master import build_audio_filter

JOBS: Dict[str, Dict[str, Any]] = {}

# ---------------------------------------------------------------------------
# Tunables (override with environment variables)
# ---------------------------------------------------------------------------
# NVENC preset: p1 (fastest) .. p7 (best). p4 is visibly the same as p5 at
# -cq 21 for talking-head footage and encodes noticeably faster.
NVENC_PRESET = os.environ.get("EXPORT_NVENC_PRESET", "p4")
# Decode the source on the GPU too. Set EXPORT_HWACCEL=none to turn off.
# If a render fails with GPU decoding on, it is retried once on the CPU.
HWACCEL = os.environ.get("EXPORT_HWACCEL", "cuda" if HAS_NVENC else "none").lower()

AUDIO_RATE = 48000      # output sample rate
AUDIO_BLOCK = 240       # audio cut granularity in samples (5 ms)
SEEK_LEAD_FRAMES = Fraction(1, 4)
MAX_INLINE_FILTER_CHARS = 24000  # Windows command lines top out at ~32k chars


def get_job_status(job_id: str) -> Optional[Dict[str, Any]]:
    return JOBS.get(job_id)


def escape_ffmpeg_path(path_str: str) -> str:
    """Escape Windows file paths for FFmpeg filter arguments."""
    p = str(Path(path_str).resolve()).replace("\\", "/")
    if len(p) > 1 and p[1] == ":":
        p = p[0] + "\\:" + p[2:]
    return p


# ---------------------------------------------------------------------------
# Probing
# ---------------------------------------------------------------------------
def _parse_rate(value: Optional[str]) -> Optional[Fraction]:
    try:
        f = Fraction(value) if value else None
        return f if f and 1 <= f <= 1000 else None
    except (ValueError, ZeroDivisionError):
        return None


def _probe_source(file_path: str) -> Dict[str, Any]:
    """Exact frame rate (as a fraction), displayed frame size, and whether audio exists."""
    info: Dict[str, Any] = {"rate": None, "width": None, "height": None, "has_audio": True}
    try:
        proc = subprocess.run(
            [FFPROBE_PATH, "-v", "quiet", "-print_format", "json", "-show_streams", file_path],
            capture_output=True, text=True, check=True
        )
        streams = json.loads(proc.stdout).get("streams", [])
    except Exception:
        return info

    info["has_audio"] = any(s.get("codec_type") == "audio" for s in streams)
    for s in streams:
        if s.get("codec_type") != "video":
            continue
        avg, nominal = _parse_rate(s.get("avg_frame_rate")), _parse_rate(s.get("r_frame_rate"))
        # Constant-frame-rate files report the same value twice; trust that.
        if avg and nominal and abs(avg - nominal) / nominal < Fraction(1, 500):
            info["rate"] = nominal
        w, h = int(s.get("width") or 0), int(s.get("height") or 0)
        rotation = 0
        for sd in s.get("side_data_list", []) or []:
            try:
                rotation = int(float(sd.get("rotation", 0)))
            except (TypeError, ValueError):
                pass
        if abs(rotation) % 180 == 90:
            w, h = h, w
        if w and h:
            info["width"], info["height"] = w, h
        break
    return info


def _pick_frame_rate(requested: float, source_rate: Optional[Fraction]) -> Fraction:
    """
    Use the source's exact rate when the request means the same thing
    (e.g. "60" or "59.94" for a 60000/1001 source). Converting 59.94 -> 60.0
    would insert a duplicate frame every ~17 seconds.
    """
    if source_rate and requested and abs(float(source_rate) - requested) / float(source_rate) < 0.002:
        return source_rate
    if not requested or requested <= 0:
        return source_rate or Fraction(30)
    return Fraction(requested).limit_denominator(1001)


def _probe_output_size(src_w: int, src_h: int, target_aspect: str, framing_mode: str) -> Optional[Tuple[int, int]]:
    """Ask FFmpeg what frame size the reframe filter produces (one blank frame, takes a moment)."""
    try:
        graph = (
            f"color=s={src_w}x{src_h}:r=1:d=1,format=yuv420p[src];"
            + build_reframe_filter("src", "out0", target_aspect=target_aspect, framing_mode=framing_mode)
        )
        proc = subprocess.run(
            [FFPROBE_PATH, "-v", "error", "-f", "lavfi", "-i", graph,
             "-show_entries", "stream=width,height", "-of", "csv=p=0"],
            capture_output=True, text=True, timeout=30
        )
        w, h = proc.stdout.strip().splitlines()[0].split(",")[:2]
        return int(w), int(h)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Cut planning
# ---------------------------------------------------------------------------
def _frame_ranges(segments: List[Dict[str, float]], rate: Fraction, skip_frames: int = 0) -> List[Tuple[int, int]]:
    """Segments (seconds) -> sorted, merged, half-open frame ranges [first, end),
    numbered from the first frame FFmpeg will actually read."""
    raw = []
    for s in segments:
        a = round(float(s["start"]) * rate) - skip_frames
        b = round(float(s["end"]) * rate) - skip_frames
        if b > max(a, 0):
            raw.append((max(a, 0), b))
    raw.sort()
    merged: List[Tuple[int, int]] = []
    for a, b in raw:
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged


def _audio_ranges(frame_ranges: List[Tuple[int, int]], rate: Fraction, lead_frames: float = 0.0) -> List[Tuple[int, int]]:
    """
    Matching audio ranges, in blocks of AUDIO_BLOCK samples.

    Each segment's audio starts at the block nearest its first video frame.
    Its length is chosen so the TOTAL audio kept so far tracks the TOTAL video
    kept so far - rounding errors therefore cancel instead of accumulating,
    and A/V sync stays within one block (5 ms) at every cut.

    lead_frames: how far (in frames) the first video frame sits after t=0 in
    the audio's timeline (non-zero when the input is opened with a seek).
    """
    blocks_per_frame = Fraction(AUDIO_RATE, AUDIO_BLOCK) / rate
    out: List[Tuple[int, int]] = []
    kept_frames = 0
    kept_blocks = 0
    prev_end = 0
    for a, b in frame_ranges:
        kept_frames += b - a
        count = round(kept_frames * blocks_per_frame) - kept_blocks
        start = max(round((a + lead_frames) * blocks_per_frame), prev_end)
        if count > 0:
            out.append((start, start + count))
            kept_blocks += count
            prev_end = start + count
    return out


def _select_expr(ranges: List[Tuple[int, int]]) -> str:
    return "+".join(f"between(n,{a},{b - 1})" for a, b in ranges)


# ---------------------------------------------------------------------------
# FFmpeg plumbing
# ---------------------------------------------------------------------------
def _ffmpeg_major_version() -> Optional[int]:
    try:
        out = subprocess.run([FFMPEG_PATH, "-version"], capture_output=True, text=True).stdout
        m = re.search(r"ffmpeg version n?(\d+)\.", out)
        return int(m.group(1)) if m else None
    except Exception:
        return None


def _filter_args(graph: str, script_path: Path) -> List[str]:
    """Pass the filter graph inline, or via a file when it is too long for a Windows command line."""
    if len(graph) <= MAX_INLINE_FILTER_CHARS:
        return ["-filter_complex", graph]
    script_path.write_text(graph, encoding="utf-8")
    major = _ffmpeg_major_version()
    if major is not None and major < 7:
        return ["-filter_complex_script", str(script_path)]
    return ["-/filter_complex", str(script_path)]  # FFmpeg 7+ (and git builds)


def _run_ffmpeg(cmd: List[str], job_id: str, total_duration: float) -> Tuple[int, str]:
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1, universal_newlines=True
    )
    time_re = re.compile(r"out_time_ms=(\d+)")  # despite the name, this is microseconds
    recent: List[str] = []
    if proc.stdout:
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            m = time_re.search(line)
            if m:
                if total_duration > 0:
                    out_sec = float(m.group(1)) / 1_000_000.0
                    pct = min(99.0, 35.0 + (out_sec / total_duration) * 64.0)
                    JOBS[job_id]["progress"] = round(pct, 1)
            elif "=" not in line or " " in line:
                # keep real log lines, skip the key=value progress chatter
                recent.append(line)
                if len(recent) > 30:
                    recent.pop(0)
    proc.wait()
    return proc.returncode, "\n".join(recent[-8:])


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------
def run_export_pipeline(
    job_id: str,
    source_file: str,
    segments: List[Dict[str, float]],
    target_aspect: str = "9:16",
    framing_mode: str = "blur_pillarbox",
    logo_overlay: bool = True,
    logo_path: Optional[str] = None,
    logo_position: str = "top_right",
    logo_opacity: float = 0.85,
    logo_scale: float = 0.22,
    lower_third_text: Optional[str] = None,
    burn_subtitles: bool = False,
    subtitle_style: str = "bold_yellow",
    normalize_audio: bool = True,
    target_lufs: float = -14.0,
    cleanup_audio: bool = True,
    resync_drift: bool = True,   # accepted for API compatibility; no longer needed
    audio_delay_ms: float = 0.0,
    fps: float = 60.0,
    output_filename: Optional[str] = None
):
    """Execute complete export pipeline with progress tracking."""
    if not output_filename:
        output_filename = f"harvick_cut_{uuid.uuid4().hex[:8]}.mp4"
    output_path = OUTPUT_DIR / output_filename
    filter_script = OUTPUT_DIR / f"{job_id}_filters.txt"

    JOBS[job_id] = {
        "status": "processing",
        "progress": 0.0,
        "step": "Initializing",
        "output_file": str(output_path),
        "output_filename": output_filename,
        "error": None
    }

    try:
        # Step 1: Subtitles (if requested). These are timed against the SOURCE.
        ass_path = None
        if burn_subtitles:
            JOBS[job_id]["step"] = "Generating Subtitles (Whisper)"
            JOBS[job_id]["progress"] = 10.0
            temp_ass = OUTPUT_DIR / f"{job_id}_subs.ass"
            sub_res = generate_subtitles(source_file, str(temp_ass), model_name="base", style=subtitle_style)
            if "error" not in sub_res and temp_ass.exists():
                ass_path = str(temp_ass)

        JOBS[job_id]["step"] = "Assembling Video Cuts & Filters"
        JOBS[job_id]["progress"] = 25.0

        src = _probe_source(source_file)
        rate = _pick_frame_rate(fps, src["rate"])
        has_audio = src["has_audio"]

        # ---- Plan the cuts ------------------------------------------------
        # Only read the part of the file that is actually used (matters for
        # short clips taken from a long recording). Subtitles need source
        # timestamps, so the start is left alone when burning them in.
        skip_frames = 0     # whole frames skipped at the start of the source
        seek = 0.0          # the same, in seconds, as handed to FFmpeg
        read_len = None
        if segments:
            first = min(float(s["start"]) for s in segments)
            last = max(float(s["end"]) for s in segments)
            if not ass_path:
                skip_frames = max(0, int((first - 1.0) * rate))
            if skip_frames:
                # Seek to a quarter-frame BEFORE the target frame so that frame
                # is certain to be the first one delivered.
                seek = float((skip_frames - SEEK_LEAD_FRAMES) / rate)
            read_len = last - seek + 1.0

        v_ranges = _frame_ranges(segments, rate, skip_frames) if segments else []
        if segments and not v_ranges:
            raise RuntimeError("No usable segments to render.")
        lead = float(SEEK_LEAD_FRAMES) if skip_frames else 0.0
        a_ranges = _audio_ranges(v_ranges, rate, lead) if v_ranges else []
        total_frames = sum(b - a for a, b in v_ranges)
        total_duration = float(total_frames / rate) if v_ranges else 0.0

        # ---- Video filter graph ------------------------------------------
        filter_parts: List[str] = []
        rate_str = f"{rate.numerator}/{rate.denominator}"

        # Constant frame rate starting at t=0, so frame number == position
        filter_parts.append(f"[0:v]fps=fps={rate_str}:start_time=0[v_cfr]")
        curr_v = "v_cfr"

        cut_filter = None
        if v_ranges:
            cut_filter = (
                f"select='{_select_expr(v_ranges)}',"
                f"setpts=N*{rate.denominator}/{rate.numerator}/TB"
            )

        def apply_cut(label: str) -> str:
            filter_parts.append(f"[{label}]{cut_filter}[v_cut]")
            return "v_cut"

        # Normally cut first so the heavier filters never touch discarded frames.
        if cut_filter and not ass_path:
            curr_v = apply_cut(curr_v)

        filter_parts.append(f"[{curr_v}]format=yuv420p[v_fmt]")
        curr_v = "v_fmt"

        filter_parts.append(build_reframe_filter(curr_v, "v_reframed", target_aspect=target_aspect, framing_mode=framing_mode))
        curr_v = "v_reframed"

        inputs_extra: List[str] = []
        if logo_overlay:
            frame_size = None
            if src["width"] and src["height"]:
                frame_size = _probe_output_size(src["width"], src["height"], target_aspect, framing_mode)
            brand_filter, extra_asset = build_branding_filter(
                curr_v,
                "v_branded",
                logo_path=logo_path,
                position=logo_position,
                opacity=logo_opacity,
                scale_ratio=logo_scale,
                lower_third_text=lower_third_text,
                frame_size=frame_size
            )
            if extra_asset:
                inputs_extra = ["-i", extra_asset]
                brand_filter = brand_filter.replace("[logo_in]", "[1:v]")
            if brand_filter:
                filter_parts.append(brand_filter)
                curr_v = "v_branded"

        if ass_path:
            # Subtitles are burned in on the source timeline, THEN the cuts are
            # made, so the captions stay attached to the words they belong to.
            filter_parts.append(f"[{curr_v}]ass='{escape_ffmpeg_path(ass_path)}'[v_subbed]")
            curr_v = "v_subbed"
            if cut_filter:
                curr_v = apply_cut(curr_v)

        # ---- Audio filter graph ------------------------------------------
        curr_a = None
        if has_audio:
            # Fixed sample rate, starting at t=0, with any gaps in the source filled,
            # so block number == position (the audio twin of the fps filter above).
            prep = f"[0:a]aresample={AUDIO_RATE}:async=1:first_pts=0"
            if a_ranges:
                prep += (
                    f",asetnsamples=n={AUDIO_BLOCK}:p=0,"
                    f"aselect='{_select_expr(a_ranges)}',"
                    f"asetpts=N/SR/TB"
                )
            filter_parts.append(prep + "[a_cut]")
            filter_parts.append(build_audio_filter(
                "a_cut",
                "a_mastered",
                normalize=normalize_audio,
                target_lufs=target_lufs,
                cleanup_artifacts=cleanup_audio,
                resync_drift=False,  # timestamps are already exact; nothing to correct
                audio_delay_ms=audio_delay_ms
            ))
            curr_a = "a_mastered"

        # ---- Command -------------------------------------------------------
        if HAS_NVENC:
            vcodec = ["-c:v", "h264_nvenc", "-preset", NVENC_PRESET, "-cq", "21"]
        else:
            vcodec = ["-c:v", "libx264", "-preset", "fast", "-crf", "21"]

        def build_cmd(hwaccel: Optional[str]) -> List[str]:
            in_opts: List[str] = []
            if hwaccel:
                in_opts += ["-hwaccel", hwaccel]
            if seek > 0:
                in_opts += ["-ss", f"{seek:.6f}"]
            if read_len is not None:
                in_opts += ["-t", f"{read_len:.3f}"]
            cmd = [
                FFMPEG_PATH, "-y",
                *in_opts, "-i", source_file,
                *inputs_extra,
                *_filter_args(";".join(filter_parts), filter_script),
                "-map", f"[{curr_v}]",
            ]
            if curr_a:
                # loudnorm works at 192 kHz internally; bring the output back to 48 kHz
                cmd += ["-map", f"[{curr_a}]", "-c:a", "aac", "-b:a", "192k", "-ar", str(AUDIO_RATE)]
            cmd += ["-fps_mode", "cfr", *vcodec, "-progress", "pipe:1", str(output_path)]
            return cmd

        JOBS[job_id]["step"] = "Rendering Video (FFmpeg)"
        JOBS[job_id]["progress"] = 35.0

        hw = HWACCEL if HWACCEL not in ("", "none", "off", "0") else None
        code, err = _run_ffmpeg(build_cmd(hw), job_id, total_duration)
        if code != 0 and hw:
            # Some sources (e.g. 10-bit 4:2:2) cannot be decoded on the GPU.
            JOBS[job_id]["step"] = "Rendering Video (FFmpeg, CPU decode)"
            JOBS[job_id]["progress"] = 35.0
            code, err = _run_ffmpeg(build_cmd(None), job_id, total_duration)
        if code != 0:
            raise RuntimeError(f"FFmpeg process failed with exit code {code}:\n{err}")

        JOBS[job_id]["status"] = "completed"
        JOBS[job_id]["progress"] = 100.0
        JOBS[job_id]["step"] = "Completed"

    except Exception as e:
        JOBS[job_id]["status"] = "error"
        JOBS[job_id]["error"] = str(e)
        JOBS[job_id]["step"] = "Failed"
    finally:
        filter_script.unlink(missing_ok=True)


def start_export_async(**kwargs) -> str:
    """Spawn export job in background thread."""
    job_id = uuid.uuid4().hex[:10]
    thread = threading.Thread(target=run_export_pipeline, kwargs={"job_id": job_id, **kwargs}, daemon=True)
    thread.start()
    return job_id
