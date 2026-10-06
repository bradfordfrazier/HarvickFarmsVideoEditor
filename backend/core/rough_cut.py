"""
Rough cut: remove silences / retakes WITHOUT re-encoding the video.

The original compressed video is copied straight into the output, so a
44-minute 4K file takes a few minutes instead of ~20, and there is no quality
loss at all. The price is precision:

- A copied segment can only START on a keyframe, so each kept segment begins
  on the keyframe at or just before its ideal start. A little of the pause
  before each segment therefore survives (up to one keyframe interval).
- Segment ENDS are accurate to within a few frames.
- No reframing, logo, lower-third, subtitles or frame-rate change: those all
  require re-encoding. Audio IS re-cut precisely and can still be mastered.

How it works
1. ffprobe lists every video packet (timestamp + keyframe flag).
2. Each kept segment is widened to a span of whole packets that can be copied
   and decoded on its own. Spans that touch are merged.
3. Each span is copied to a small temporary video-only file.
4. One final FFmpeg run joins those files (still copying) and adds audio that
   is cut on a 5 ms grid to exactly the same timeline, so A/V sync is exact
   by construction.
"""
import shutil
import subprocess
from bisect import bisect_right
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from backend.config import FFMPEG_PATH, FFPROBE_PATH, OUTPUT_DIR
from backend.core.audio_master import build_audio_filter

# A segment may start up to this much late / end this much early if that lets
# it land on a keyframe. Keep it below the speech padding used when scanning.
SNAP_SLACK_SECONDS = 0.10
EXTRACT_WORKERS = 4
AUDIO_RATE = 48000
AUDIO_BLOCK = 240   # 5 ms

_PACKET_CACHE: Dict[Tuple[str, int, int], Tuple[List[float], List[bool]]] = {}


class RoughCutUnsupported(RuntimeError):
    """The source cannot be cut cleanly without re-encoding."""


# ---------------------------------------------------------------------------
# Packet table
# ---------------------------------------------------------------------------
def probe_video_packets(file_path: str) -> Tuple[List[float], List[bool]]:
    """Presentation time and keyframe flag of every video packet, in decode order."""
    p = Path(file_path)
    st = p.stat()
    cache_key = (str(p.resolve()), st.st_mtime_ns, st.st_size)
    if cache_key in _PACKET_CACHE:
        return _PACKET_CACHE[cache_key]

    proc = subprocess.run(
        [FFPROBE_PATH, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "packet=pts_time,dts_time,flags", "-of", "csv=p=0", file_path],
        capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe could not read the video packets:\n{proc.stderr.strip()[-400:]}")

    pts: List[float] = []
    key: List[bool] = []
    for line in proc.stdout.splitlines():
        parts = line.strip().split(",")
        if len(parts) < 3:
            continue
        try:
            t = float(parts[0])
        except ValueError:
            try:
                t = float(parts[1])
            except ValueError:
                continue
        pts.append(t)
        key.append("K" in parts[2])
    if not pts:
        raise RuntimeError("No video packets found in the source file.")
    _PACKET_CACHE.clear()  # keep at most one file's table in memory
    _PACKET_CACHE[cache_key] = (pts, key)
    return pts, key


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------
def plan_rough_cut(
    segments: List[Dict[str, float]],
    pts: List[float],
    key: List[bool],
    slack: float = SNAP_SLACK_SECONDS
) -> Dict[str, Any]:
    """
    Turn ideal segments into copyable spans.

    Returns {"spans": [{"first", "count", "start", "end"}], "keyframe_interval"}.
    first/count index into the packet list; start/end are source seconds.
    """
    n = len(pts)

    # Typical frame duration (used only for the very last frame's end time)
    ordered = sorted(pts)
    gaps = sorted(b - a for a, b in zip(ordered, ordered[1:]) if b > a)
    frame_dur = gaps[len(gaps) // 2] if gaps else 1 / 30
    end_of_video = ordered[-1] + frame_dur

    # suffix_min[c] = earliest display time among packets c..end
    suffix_min = [0.0] * (n + 1)
    suffix_min[n] = end_of_video
    for i in range(n - 1, -1, -1):
        suffix_min[i] = min(pts[i], suffix_min[i + 1])

    # Usable start points: keyframes that nothing later in the file is shown
    # before ("closed GOP"). A span starting on any other keyframe would begin
    # with frames that depend on video we have cut away.
    starts = [i for i in range(n) if key[i] and suffix_min[i] >= pts[i]]
    if len(starts) < 2 and n > 600:
        raise RoughCutUnsupported(
            "This video has too few independent keyframes to cut without re-encoding "
            "(open-GOP or single-keyframe encoding). Use the full render instead."
        )
    if not starts:
        raise RoughCutUnsupported("No usable keyframe found. Use the full render instead.")
    start_pts = [pts[i] for i in starts]
    kf_gaps = sorted(b - a for a, b in zip(start_pts, start_pts[1:]))
    keyframe_interval = kf_gaps[len(kf_gaps) // 2] if kf_gaps else 0.0

    spans: List[List[int]] = []  # [first_packet, end_packet)
    for seg in sorted(segments, key=lambda s: float(s["start"])):
        s, e = float(seg["start"]), float(seg["end"])
        if e <= s:
            continue
        j = bisect_right(start_pts, s + slack) - 1
        first = starts[max(j, 0)]

        # Earliest point where the copy can stop cleanly: everything copied so
        # far is displayed before everything not yet copied.
        running_max = pts[first]
        c = first + 1
        while c < n:
            if running_max < suffix_min[c] and suffix_min[c] >= e - slack:
                break
            running_max = max(running_max, pts[c])
            c += 1

        if spans and first <= spans[-1][1]:
            spans[-1][1] = max(spans[-1][1], c)
        else:
            spans.append([first, c])

    return {
        "spans": [
            {"first": a, "count": b - a, "start": pts[a], "end": suffix_min[b]}
            for a, b in spans if b > a
        ],
        "keyframe_interval": keyframe_interval,
    }


def _audio_block_ranges(spans: List[Dict[str, Any]]) -> List[Tuple[int, int]]:
    """Audio ranges (5 ms blocks) whose running total tracks the video's, so sync cannot drift."""
    per_sec = AUDIO_RATE / AUDIO_BLOCK
    out: List[Tuple[int, int]] = []
    kept_time = 0.0
    kept_blocks = 0
    prev_end = 0
    for sp in spans:
        kept_time += sp["end"] - sp["start"]
        count = round(kept_time * per_sec) - kept_blocks
        start = max(round(sp["start"] * per_sec), prev_end)
        if count > 0:
            out.append((start, start + count))
            kept_blocks += count
            prev_end = start + count
    return out


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------
def _video_tag(file_path: str) -> Optional[str]:
    """Keep the source's codec tag (e.g. hvc1) so the output opens in the same apps."""
    try:
        out = subprocess.run(
            [FFPROBE_PATH, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_tag_string", "-of", "csv=p=0", file_path],
            capture_output=True, text=True
        ).stdout.strip()
        return out if out in ("hvc1", "hev1", "avc1", "avc3") else None
    except Exception:
        return None


def _extract_span(source_file: str, span: Dict[str, Any], dest: Path) -> None:
    # Seek a hair before the keyframe; FFmpeg then starts the copy exactly on it.
    seek = max(0.0, span["start"] - 0.0001)
    proc = subprocess.run(
        [FFMPEG_PATH, "-v", "error", "-y",
         "-ss", f"{seek:.6f}", "-i", source_file,
         "-map", "0:v:0", "-c", "copy", "-copypriorss", "0", "-frames:v", str(span["count"]),
         str(dest)],
        capture_output=True, text=True
    )
    if proc.returncode != 0 or not dest.exists():
        raise RuntimeError(f"Could not copy segment at {span['start']:.2f}s:\n{proc.stderr.strip()[-400:]}")


def run_rough_cut(
    job: Dict[str, Any],
    job_id: str,
    source_file: str,
    segments: List[Dict[str, float]],
    output_path: Path,
    has_audio: bool,
    run_ffmpeg: Callable[[List[str], str, float, float, float], Tuple[int, str]],
    select_expr: Callable[[List[Tuple[int, int]]], str],
    filter_args: Callable[[str, Path], List[str]],
    normalize_audio: bool = True,
    target_lufs: float = -14.0,
    cleanup_audio: bool = True,
    audio_delay_ms: float = 0.0,
) -> None:
    work_dir = OUTPUT_DIR / f"{job_id}_roughcut"
    filter_script = OUTPUT_DIR / f"{job_id}_filters.txt"
    try:
        job["step"] = "Rough Cut: Indexing Keyframes"
        job["progress"] = 5.0
        pts, key = probe_video_packets(source_file)
        plan = plan_rough_cut(segments, pts, key)
        spans = plan["spans"]
        if not spans:
            raise RuntimeError("No usable segments to render.")

        total = sum(sp["end"] - sp["start"] for sp in spans)
        ideal = sum(float(s["end"]) - float(s["start"]) for s in segments)
        job["info"] = (
            f"Rough cut keeps {total:.1f}s in {len(spans)} pieces "
            f"(the precise cut would keep {ideal:.1f}s in {len(segments)}). "
            f"Keyframes are {plan['keyframe_interval']:.2f}s apart."
        )

        # ---- Stage 1: copy each span to its own file ----------------------
        work_dir.mkdir(parents=True, exist_ok=True)
        job["step"] = "Rough Cut: Copying Segments"
        done = 0

        def work(item):
            idx, sp = item
            _extract_span(source_file, sp, work_dir / f"p{idx:05d}.mp4")

        with ThreadPoolExecutor(max_workers=EXTRACT_WORKERS) as pool:
            for _ in pool.map(work, enumerate(spans)):
                done += 1
                job["progress"] = round(10.0 + 45.0 * done / len(spans), 1)

        # ---- Stage 2: join + audio ----------------------------------------
        lines = ["ffconcat version 1.0"]
        for idx, sp in enumerate(spans):
            lines.append(f"file 'p{idx:05d}.mp4'")
            lines.append(f"duration {sp['end'] - sp['start']:.6f}")
        concat_list = work_dir / "list.txt"
        concat_list.write_text("\n".join(lines), encoding="utf-8")

        cmd = [FFMPEG_PATH, "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list)]
        if has_audio:
            graph = (
                f"[1:a]aresample={AUDIO_RATE}:async=1:first_pts=0,"
                f"asetnsamples=n={AUDIO_BLOCK}:p=0,"
                f"aselect='{select_expr(_audio_block_ranges(spans))}',"
                f"asetpts=N/SR/TB[a_cut];"
                + build_audio_filter(
                    "a_cut", "a_out",
                    normalize=normalize_audio, target_lufs=target_lufs,
                    cleanup_artifacts=cleanup_audio, resync_drift=False,
                    audio_delay_ms=audio_delay_ms
                )
            )
            cmd += ["-vn", "-i", source_file, *filter_args(graph, filter_script),
                    "-map", "0:v:0", "-map", "[a_out]",
                    "-c:a", "aac", "-b:a", "192k", "-ar", str(AUDIO_RATE)]
        else:
            cmd += ["-map", "0:v:0"]
        cmd += ["-c:v", "copy"]
        tag = _video_tag(source_file)
        if tag:
            cmd += ["-tag:v", tag]
        cmd += ["-progress", "pipe:1", str(output_path)]

        job["step"] = "Rough Cut: Joining & Audio"
        code, err = run_ffmpeg(cmd, job_id, total, 55.0, 44.0)
        if code != 0:
            raise RuntimeError(f"FFmpeg process failed with exit code {code}:\n{err}")
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
        filter_script.unlink(missing_ok=True)
