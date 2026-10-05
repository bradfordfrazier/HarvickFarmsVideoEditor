import os
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Optional
from backend.config import FFMPEG_PATH

def format_timestamp_ass(seconds: float) -> str:
    """Convert float seconds to ASS format: H:MM:SS.cs"""
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    centis = int(round((seconds - int(seconds)) * 100))
    return f"{hrs}:{mins:02d}:{secs:02d}.{centis:02d}"

def format_timestamp_srt(seconds: float) -> str:
    """Convert float seconds to SRT format: HH:MM:SS,mmm"""
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    return f"{hrs:02d}:{mins:02d}:{secs:02d},{millis:03d}"

def generate_subtitles(
    video_path: str,
    output_ass_path: str,
    model_name: str = "base",
    style: str = "bold_yellow",
    font_size: int = 18
) -> Dict[str, Any]:
    """Transcribe video using Whisper and write styled ASS & SRT subtitles."""
    try:
        import whisper
    except ImportError:
        return {"error": "Whisper is not installed. Subtitles skipped."}

    # Extract temporary audio for fast transcription
    temp_wav = Path(output_ass_path).with_suffix(".temp.wav")
    cmd = [
        FFMPEG_PATH,
        "-y",
        "-i", video_path,
        "-vn",
        "-ac", "1",
        "-ar", "16000",
        str(temp_wav)
    ]
    subprocess.run(cmd, capture_output=True, check=True)

    try:
        model = whisper.load_model(model_name)
        result = model.transcribe(str(temp_wav), word_timestamps=True, fp16=False)
    finally:
        if temp_wav.exists():
            temp_wav.unlink(missing_ok=True)

    segments = result.get("segments", [])
    
    # Define ASS Styles
    # PrimaryColor format in ASS is &HAABBGGRR (hex)
    # Bold Yellow: &H0000FFFF (BGR: BB=00, GG=FF, RR=FF -> Yellow)
    # White: &H00FFFFFF
    primary_color = "&H0000FFFF" if style == "bold_yellow" else "&H00FFFFFF"

    ass_header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Impact,{font_size * 3},{primary_color},&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,1,0,1,4.0,2.0,2,40,40,280,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ass_events = []
    srt_entries = []
    srt_idx = 1

    for seg in segments:
        words = seg.get("words", [])
        if words:
            # Chunk words into punchy short phrases of 3-4 words for dynamic social style
            chunk_size = 4
            for i in range(0, len(words), chunk_size):
                chunk = words[i : i + chunk_size]
                start_t = chunk[0]["start"]
                end_t = chunk[-1]["end"]
                text = " ".join(w["word"].strip() for w in chunk).upper()

                ass_start = format_timestamp_ass(start_t)
                ass_end = format_timestamp_ass(end_t)
                ass_events.append(f"Dialogue: 0,{ass_start},{ass_end},Default,,0,0,0,,{text}")

                srt_start = format_timestamp_srt(start_t)
                srt_end = format_timestamp_srt(end_t)
                srt_entries.append(f"{srt_idx}\n{srt_start} --> {srt_end}\n{text}\n")
                srt_idx += 1
        else:
            # Fallback to segment-level
            start_t = seg["start"]
            end_t = seg["end"]
            text = seg["text"].strip().upper()
            ass_start = format_timestamp_ass(start_t)
            ass_end = format_timestamp_ass(end_t)
            ass_events.append(f"Dialogue: 0,{ass_start},{ass_end},Default,,0,0,0,,{text}")

            srt_start = format_timestamp_srt(start_t)
            srt_end = format_timestamp_srt(end_t)
            srt_entries.append(f"{srt_idx}\n{srt_start} --> {srt_end}\n{text}\n")
            srt_idx += 1

    # Write ASS
    Path(output_ass_path).write_text(ass_header + "\n".join(ass_events), encoding="utf-8")

    # Write SRT
    srt_path = Path(output_ass_path).with_suffix(".srt")
    srt_path.write_text("\n".join(srt_entries), encoding="utf-8")

    return {
        "ass_path": str(output_ass_path),
        "srt_path": str(srt_path),
        "segment_count": len(ass_events),
        "text_preview": result.get("text", "")[:200]
    }
