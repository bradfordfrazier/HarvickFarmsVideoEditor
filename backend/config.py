import os
import shutil
import subprocess
import sys
from pathlib import Path

IS_FROZEN = getattr(sys, "frozen", False)

if IS_FROZEN:
    # When packaged with PyInstaller, internal files reside in _MEIPASS or executable directory
    BASE_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    
    # Store dynamic writable data in AppData and user's Videos folder
    APP_DATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "HarvickFarmsVideoEditor"
    UPLOADS_DIR = APP_DATA_DIR / "uploads"
    
    videos_folder = Path.home() / "Videos"
    OUTPUT_DIR = (videos_folder / "HarvickFarmsStudio") if videos_folder.exists() else (APP_DATA_DIR / "output")
else:
    BASE_DIR = Path(__file__).resolve().parent.parent
    UPLOADS_DIR = BASE_DIR / "uploads"
    OUTPUT_DIR = BASE_DIR / "output"

BACKEND_DIR = BASE_DIR / "backend"
FRONTEND_DIR = BASE_DIR / "frontend"
ASSETS_DIR = BASE_DIR / "assets"
PRESETS_DIR = BASE_DIR / "presets"

for d in [UPLOADS_DIR, OUTPUT_DIR]:
    d.mkdir(parents=True, exist_ok=True)

if not IS_FROZEN:
    for d in [ASSETS_DIR, PRESETS_DIR]:
        d.mkdir(parents=True, exist_ok=True)

# Locate FFmpeg & FFprobe (bundled bin/ directory has highest priority)
BUNDLED_BIN_DIR = BASE_DIR / "bin"
bundled_ffmpeg = BUNDLED_BIN_DIR / "ffmpeg.exe"
bundled_ffprobe = BUNDLED_BIN_DIR / "ffprobe.exe"

FFMPEG_PATH = str(bundled_ffmpeg) if bundled_ffmpeg.exists() else (shutil.which("ffmpeg") or "ffmpeg")
FFPROBE_PATH = str(bundled_ffprobe) if bundled_ffprobe.exists() else (shutil.which("ffprobe") or "ffprobe")

def detect_nvenc_support() -> bool:
    """Check if NVIDIA NVENC hardware acceleration is functional on this system."""
    try:
        result = subprocess.run(
            [
                FFMPEG_PATH, "-y",
                "-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.04",
                "-c:v", "h264_nvenc",
                "-f", "null", "-"
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=4
        )
        return result.returncode == 0
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
