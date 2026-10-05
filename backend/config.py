import os
import shutil
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
BACKEND_DIR = BASE_DIR / "backend"
FRONTEND_DIR = BASE_DIR / "frontend"
ASSETS_DIR = BASE_DIR / "assets"
PRESETS_DIR = BASE_DIR / "presets"
UPLOADS_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "output"

for d in [UPLOADS_DIR, OUTPUT_DIR, ASSETS_DIR, PRESETS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

FFMPEG_PATH = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE_PATH = shutil.which("ffprobe") or "ffprobe"

def detect_nvenc_support() -> bool:
    """Check if NVIDIA NVENC hardware acceleration is available in FFmpeg."""
    try:
        result = subprocess.run(
            [FFMPEG_PATH, "-encoders"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5
        )
        return "h264_nvenc" in result.stdout
    except Exception:
        return False

HAS_NVENC = detect_nvenc_support()

DEFAULT_SETTINGS = {
    "silence_thresh_db": -35.0,
    "min_silence_duration": 0.45,
    "padding_duration": 0.12,
    "target_aspect_ratio": "9:16",
    "framing_mode": "blur_pillarbox", # blur_pillarbox, crop, fit
    "burn_subtitles": False,
    "subtitle_style": "bold_yellow",
    "normalize_audio": True,
    "target_lufs": -14.0,
    "logo_overlay": True,
    "logo_position": "top_right",
    "logo_opacity": 0.85,
    "logo_scale": 0.18,
    "video_codec": "h264_nvenc" if HAS_NVENC else "libx264"
}
