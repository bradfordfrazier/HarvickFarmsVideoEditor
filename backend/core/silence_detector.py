import re
import json
import subprocess
from typing import List, Dict, Any, Tuple
from pathlib import Path
from backend.config import FFMPEG_PATH, FFPROBE_PATH

def get_media_metadata(file_path: str) -> Dict[str, Any]:
    """Extract media metadata using ffprobe."""
    cmd = [
        FFPROBE_PATH,
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        file_path
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(proc.stdout)
    except Exception as e:
        return {
            "error": str(e),
            "duration": 0.0,
            "width": 1920,
            "height": 1080,
            "fps": 30.0,
            "has_audio": False
        }

    format_info = data.get("format", {})
    duration = float(format_info.get("duration", 0.0))
    
    video_stream = None
    audio_stream = None
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video" and not video_stream:
            video_stream = stream
        elif stream.get("codec_type") == "audio" and not audio_stream:
            audio_stream = stream

    width = 1920
    height = 1080
    fps = 30.0
    if video_stream:
        width = int(video_stream.get("width", 1920))
        height = int(video_stream.get("height", 1080))
        r_frame_rate = video_stream.get("r_frame_rate", "30/1")
        if "/" in r_frame_rate:
            num, den = r_frame_rate.split("/")
            fps = float(num) / max(float(den), 1.0)
        else:
            fps = float(r_frame_rate or 30.0)

    has_audio = audio_stream is not None

    return {
        "duration": duration,
        "width": width,
        "height": height,
        "fps": round(fps, 2),
        "has_audio": has_audio,
        "filename": Path(file_path).name,
        "file_path": file_path
    }

def detect_silence_intervals(
    file_path: str,
    noise_db: float = -35.0,
    min_duration: float = 0.45
) -> List[Dict[str, float]]:
    """Detect periods of silence using FFmpeg silencedetect with -vn (audio only, 1000x faster)."""
    cmd = [
        FFMPEG_PATH,
        "-vn",
        "-i", file_path,
        "-af", f"silencedetect=noise={noise_db}dB:d={min_duration}",
        "-f", "null",
        "-"
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    output = proc.stderr

    silence_intervals: List[Dict[str, float]] = []
    current_start = None

    start_re = re.compile(r"silence_start:\s*([0-9\.]+)")
    end_re = re.compile(r"silence_end:\s*([0-9\.]+)\s*\|\s*silence_duration:\s*([0-9\.]+)")

    for line in output.splitlines():
        start_match = start_re.search(line)
        if start_match:
            current_start = float(start_match.group(1))

        end_match = end_re.search(line)
        if end_match:
            silence_end = float(end_match.group(1))
            silence_dur = float(end_match.group(2))
            start_val = current_start if current_start is not None else max(0.0, silence_end - silence_dur)
            silence_intervals.append({
                "start": round(start_val, 3),
                "end": round(silence_end, 3),
                "duration": round(silence_dur, 3)
            })
            current_start = None

    return silence_intervals

def compute_speech_segments(
    total_duration: float,
    silence_intervals: List[Dict[str, float]],
    padding: float = 0.12,
    min_speech_len: float = 0.2
) -> List[Dict[str, float]]:
    """Invert silence intervals into active speech segments, applying padding around speech."""
    if not silence_intervals:
        return [{"start": 0.0, "end": round(total_duration, 3), "duration": round(total_duration, 3)}]

    # Calculate raw speech intervals
    speech_spans: List[Tuple[float, float]] = []
    cursor = 0.0

    for s in silence_intervals:
        s_start = s["start"]
        s_end = s["end"]
        
        # Speech before silence
        if s_start > cursor:
            padded_start = max(0.0, cursor - (padding if cursor > 0 else 0.0))
            padded_end = min(total_duration, s_start + padding)
            if padded_end > padded_start:
                speech_spans.append((padded_start, padded_end))
        
        cursor = max(cursor, s_end)

    # Tail after final silence
    if cursor < total_duration:
        padded_start = max(0.0, cursor - padding)
        padded_end = total_duration
        if padded_end > padded_start:
            speech_spans.append((padded_start, padded_end))

    if not speech_spans:
        return []

    # Merge overlapping or adjacent padded speech spans
    merged_spans: List[Tuple[float, float]] = []
    curr_start, curr_end = speech_spans[0]

    for next_start, next_end in speech_spans[1:]:
        if next_start <= curr_end:
            # Overlapping or contiguous
            curr_end = max(curr_end, next_end)
        else:
            merged_spans.append((curr_start, curr_end))
            curr_start, curr_end = next_start, next_end
    merged_spans.append((curr_start, curr_end))

    # Format result segments
    result = []
    for start, end in merged_spans:
        dur = end - start
        if dur >= min_speech_len:
            result.append({
                "start": round(start, 3),
                "end": round(end, 3),
                "duration": round(dur, 3)
            })

    return result

_WAVEFORM_CACHE: Dict[str, List[float]] = {}

def extract_waveform_envelope(file_path: str, points: int = 500) -> List[float]:
    """Generate downsampled waveform amplitude envelope (0.0 to 1.0) for visual timeline."""
    cache_key = f"{file_path}_{points}"
    if cache_key in _WAVEFORM_CACHE:
        return _WAVEFORM_CACHE[cache_key]

    try:
        # Extract mono 8kHz 16-bit raw PCM
        cmd = [
            FFMPEG_PATH,
            "-vn",
            "-i", file_path,
            "-vn",
            "-ac", "1",
            "-ar", "8000",
            "-f", "s16le",
            "-"
        ]
        proc = subprocess.run(cmd, capture_output=True, check=True)
        raw_bytes = proc.stdout
        
        import array
        samples = array.array('h')
        samples.frombytes(raw_bytes)
        total_samples = len(samples)
        
        if total_samples == 0:
            return [0.0] * points
            
        step = max(1, total_samples // points)
        peaks = []
        for i in range(points):
            chunk = samples[i * step : (i + 1) * step]
            if chunk:
                # Max absolute normalized amplitude
                max_val = max(abs(x) for x in chunk)
                peaks.append(round(min(1.0, max_val / 32768.0), 3))
            else:
                peaks.append(0.0)
        _WAVEFORM_CACHE[cache_key] = peaks
        return peaks
    except Exception:
        return [0.1] * points

def analyze_video(
    file_path: str,
    noise_db: float = -35.0,
    min_silence_duration: float = 0.45,
    padding: float = 0.12,
    detect_retakes: bool = False,
    similarity_threshold: float = 0.55
) -> Dict[str, Any]:
    """Comprehensive video scan: metadata, silence intervals, speech segments, waveform, and optional retake detection."""
    meta = get_media_metadata(file_path)
    total_dur = meta.get("duration", 0.0)

    if not meta.get("has_audio") or total_dur <= 0.0:
        return {
            "metadata": meta,
            "silence_intervals": [],
            "speech_segments": [{"start": 0.0, "end": total_dur, "duration": total_dur}],
            "total_speech_duration": total_dur,
            "total_silence_duration": 0.0,
            "time_saved_seconds": 0.0,
            "time_saved_percent": 0.0,
            "waveform": [0.0] * 200,
            "retake_groups": [],
            "discarded_retakes": [],
            "total_retakes_removed": 0
        }

    silence_intervals = detect_silence_intervals(file_path, noise_db, min_silence_duration)
    speech_segments = compute_speech_segments(total_dur, silence_intervals, padding)

    retake_groups = []
    discarded_retakes = []
    total_retakes_removed = 0
    raw_speech_segments = list(speech_segments)

    if detect_retakes:
        try:
            from backend.core.take_detector import (
                transcribe_audio_whisper,
                detect_repeated_takes,
                filter_speech_segments_by_retakes
            )
            transcribed = transcribe_audio_whisper(file_path)
            retake_res = detect_repeated_takes(transcribed, similarity_threshold=similarity_threshold)
            retake_groups = retake_res["groups"]
            discarded_retakes = retake_res["discarded_intervals"]
            total_retakes_removed = retake_res["total_retakes_removed"]
            speech_segments = filter_speech_segments_by_retakes(speech_segments, discarded_retakes)
        except Exception as e:
            print(f"Warning: Retake detection failed: {e}")

    total_speech_dur = sum(seg["duration"] for seg in speech_segments)
    total_silence_dur = max(0.0, total_dur - total_speech_dur)
    time_saved_pct = (total_silence_dur / total_dur * 100.0) if total_dur > 0 else 0.0

    waveform = extract_waveform_envelope(file_path, points=400)

    return {
        "metadata": meta,
        "silence_intervals": silence_intervals,
        "speech_segments": speech_segments,
        "raw_speech_segments": raw_speech_segments,
        "total_speech_duration": round(total_speech_dur, 2),
        "total_silence_duration": round(total_silence_dur, 2),
        "time_saved_seconds": round(total_silence_dur, 2),
        "time_saved_percent": round(time_saved_pct, 1),
        "waveform": waveform,
        "retake_groups": retake_groups,
        "discarded_retakes": discarded_retakes,
        "total_retakes_removed": total_retakes_removed
    }
