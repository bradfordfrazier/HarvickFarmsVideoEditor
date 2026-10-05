import os
import re
import uuid
import threading
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable
from backend.config import FFMPEG_PATH, OUTPUT_DIR, HAS_NVENC
from backend.core.video_reframer import build_reframe_filter
from backend.core.branding_engine import build_branding_filter
from backend.core.subtitle_generator import generate_subtitles
from backend.core.audio_master import build_audio_filter

JOBS: Dict[str, Dict[str, Any]] = {}

def get_job_status(job_id: str) -> Optional[Dict[str, Any]]:
    return JOBS.get(job_id)

def escape_ffmpeg_path(path_str: str) -> str:
    """Escape Windows file paths for FFmpeg filter arguments."""
    p = str(Path(path_str).resolve()).replace("\\", "/")
    # Escape colon after drive letter e.g. C: -> C\:
    if len(p) > 1 and p[1] == ":":
        p = p[0] + "\\:" + p[2:]
    return p

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
    resync_drift: bool = True,
    audio_delay_ms: float = 0.0,
    fps: float = 60.0,
    output_filename: Optional[str] = None
):
    """Execute complete export pipeline with progress tracking."""
    if not output_filename:
        output_filename = f"harvick_cut_{uuid.uuid4().hex[:8]}.mp4"
    output_path = OUTPUT_DIR / output_filename

    JOBS[job_id] = {
        "status": "processing",
        "progress": 0.0,
        "step": "Initializing",
        "output_file": str(output_path),
        "output_filename": output_filename,
        "error": None
    }

    try:
        # Step 1: Subtitles (if requested)
        ass_path = None
        if burn_subtitles:
            JOBS[job_id]["step"] = "Generating Subtitles (Whisper)"
            JOBS[job_id]["progress"] = 10.0
            temp_ass = OUTPUT_DIR / f"{job_id}_subs.ass"
            sub_res = generate_subtitles(source_file, str(temp_ass), model_name="base", style=subtitle_style)
            if "error" not in sub_res:
                ass_path = str(temp_ass)

        JOBS[job_id]["step"] = "Assembling Video Cuts & Filters"
        JOBS[job_id]["progress"] = 25.0

        # Construct FFmpeg command using ffconcat demuxer (eliminates command line limits and RAM buffering)
        ffconcat_path = None
        if segments:
            ffconcat_path = OUTPUT_DIR / f"{job_id}_concat.txt"
            video_path_str = Path(source_file).resolve().as_posix().replace("'", "'\\''")
            concat_lines = ["ffconcat version 1.0"]
            for s in segments:
                concat_lines.append(f"file '{video_path_str}'")
                concat_lines.append(f"inpoint {s['start']}")
                concat_lines.append(f"outpoint {s['end']}")
            ffconcat_path.write_text("\n".join(concat_lines), encoding="utf-8")
            inputs = ["-fflags", "+genpts+igndts", "-safe", "0", "-f", "concat", "-i", str(ffconcat_path)]
        else:
            inputs = ["-fflags", "+genpts+igndts", "-i", source_file]

        curr_v = "0:v"
        curr_a = "0:a"
        filter_parts: List[str] = []

        # 1. Enforce Constant Frame Rate (CFR) to prevent VFR sync drift
        filter_parts.append(f"[{curr_v}]fps=fps={fps},format=yuv420p[v_cfr]")
        curr_v = "v_cfr"

        # 2. Reframing
        reframe_str = build_reframe_filter(curr_v, "v_reframed", target_aspect=target_aspect, framing_mode=framing_mode)
        filter_parts.append(reframe_str)
        curr_v = "v_reframed"

        # 3. Branding
        if logo_overlay:
            brand_filter, extra_asset = build_branding_filter(
                curr_v,
                "v_branded",
                logo_path=logo_path,
                position=logo_position,
                opacity=logo_opacity,
                scale_ratio=logo_scale,
                lower_third_text=lower_third_text
            )
            if extra_asset:
                inputs.extend(["-i", extra_asset])
                # The extra input index is 1
                brand_filter = brand_filter.replace("[logo_in]", "[1:v]")
            filter_parts.append(brand_filter)
            curr_v = "v_branded"

        # 4. Subtitle burn-in
        if ass_path and Path(ass_path).exists():
            escaped_ass = escape_ffmpeg_path(ass_path)
            filter_parts.append(f"[{curr_v}]ass='{escaped_ass}'[v_subbed]")
            curr_v = "v_subbed"

        # 5. Audio mastering with artifact cleanup and A/V drift-locking resampler
        audio_filter = build_audio_filter(
            curr_a,
            "a_mastered",
            normalize=normalize_audio,
            target_lufs=target_lufs,
            cleanup_artifacts=cleanup_audio,
            resync_drift=resync_drift,
            audio_delay_ms=audio_delay_ms
        )
        filter_parts.append(audio_filter)
        curr_a = "a_mastered"

        total_est_duration = sum(seg["duration"] for seg in segments) if segments else 60.0

        # Video encoder selection
        vcodec = ["-c:v", "h264_nvenc", "-preset", "p5", "-cq", "21"] if HAS_NVENC else ["-c:v", "libx264", "-preset", "fast", "-crf", "21"]

        cmd = [
            FFMPEG_PATH,
            "-y",
            *inputs,
            "-filter_complex", ";".join(filter_parts),
            "-map", f"[{curr_v}]",
            "-map", f"[{curr_a}]",
            "-fps_mode", "cfr",
            *vcodec,
            "-c:a", "aac",
            "-b:a", "192k",
            "-progress", "pipe:1",
            str(output_path)
        ]

        JOBS[job_id]["step"] = "Rendering Video (FFmpeg)"
        JOBS[job_id]["progress"] = 35.0

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True
        )

        time_re = re.compile(r"out_time_ms=(\d+)")
        recent_lines = []

        if proc.stdout:
            for line in proc.stdout:
                line = line.strip()
                if line:
                    recent_lines.append(line)
                    if len(recent_lines) > 30:
                        recent_lines.pop(0)
                m = time_re.search(line)
                if m:
                    out_ms = float(m.group(1))
                    out_sec = out_ms / 1000000.0
                    if total_est_duration > 0:
                        pct = min(99.0, 35.0 + (out_sec / total_est_duration) * 64.0)
                        JOBS[job_id]["progress"] = round(pct, 1)

        proc.wait()

        if proc.returncode != 0:
            err_details = "\n".join(recent_lines[-8:])
            raise RuntimeError(f"FFmpeg process failed with exit code {proc.returncode}:\n{err_details}")

        JOBS[job_id]["status"] = "completed"
        JOBS[job_id]["progress"] = 100.0
        JOBS[job_id]["step"] = "Completed"

    except Exception as e:
        JOBS[job_id]["status"] = "error"
        JOBS[job_id]["error"] = str(e)
        JOBS[job_id]["step"] = "Failed"
    finally:
        if ffconcat_path and ffconcat_path.exists():
            ffconcat_path.unlink(missing_ok=True)

def start_export_async(**kwargs) -> str:
    """Spawn export job in background thread."""
    job_id = uuid.uuid4().hex[:10]
    thread = threading.Thread(target=run_export_pipeline, kwargs={"job_id": job_id, **kwargs}, daemon=True)
    thread.start()
    return job_id
