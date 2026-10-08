# Harvick Farms Video Studio & Automated Editor 🎬🌾

An automated video editing, silence-removal, reframing, and branding pipeline tailored for **Harvick Farms** social content (YouTube Shorts, Instagram Reels, Long-form Vlogs).

![Harvick Farms Logo](assets/harvick_farms_logo.png)

---

## ✨ Features

- **⚡ Lightning-Fast Audio Silence Detection**: Analyzes audio tracks at 950x real-time speed using FFmpeg's `silencedetect` with in-memory waveform envelope caching.
- **✂️ Smart Jump-Cut Generation**: Seamlessly trims dead pauses and background silence with configurable dB thresholds, minimum pause lengths, and breathing room speech padding.
- **🔄 Smart Video Reframing**: Converts landscape 16:9 farm footage into 9:16 vertical shorts with cinematic blurred-pillarbox backgrounds, center zoom crop, or letterbox fit.
- **🌾 Harvick Farms Branding & Watermarks**: Automatically overlays high-resolution Harvick Farms crests and custom lower-third banners with customizable opacity and position.
- **🔊 EBU R128 Audio Loudness Mastering**: Professional broadcast-standard audio normalization (-14.0 LUFS) for balanced voice levels across platforms.
- **🚀 GPU Hardware Acceleration**: Automatic NVIDIA NVENC (`h264_nvenc`) hardware detection and acceleration.
- **🎛️ Full Interactive Studio UI**: Modern dark-mode web application featuring real-time audio waveform visualization, live jump-cut preview player, quick presets, and export tracking.

---

## 🛠️ Architecture

```
HarvickFarmsVideoEditor/
├── backend/
│   ├── app.py                     # FastAPI application & static mounts
│   ├── config.py                  # Hardware detection & default studio settings
│   ├── core/
│   │   ├── audio_master.py        # EBU R128 loudness normalization
│   │   ├── branding_engine.py     # Logo overlay & lower-third banners
│   │   ├── exporter.py            # FFmpeg concat demuxer export engine
│   │   ├── silence_detector.py    # High-speed silence detection & waveform
│   │   ├── subtitle_generator.py  # Optional Whisper subtitle burn-in
│   │   └── video_reframer.py      # Aspect ratio transforms (9:16, 1:1, 16:9)
│   └── routes/
│       └── api.py                 # REST API endpoints (/api/scan, /api/render, etc.)
├── frontend/
│   ├── index.html                 # Studio workspace interface
│   ├── css/
│   │   └── style.css              # Harvick Farms dark-mode design system
│   └── js/
│       ├── app.js                 # Studio controller & API integration
│       └── timeline.js            # HTML5 Canvas interactive audio waveform
├── presets/
│   ├── youtube_shorts_reframe.json# 9:16 vertical shorts preset
│   └── longform_clean_cut.json    # 16:9 landscape clean-cut preset
├── assets/
│   ├── harvick_farms_logo.png     # High-resolution watermark badge
│   └── harvick_farms_logo.svg     # Scalable vector logo
└── tests/
    └── test_pipeline.py           # Pytest automated test suite
```

---

## 🚀 Getting Started

### Prerequisites

- **Python 3.10+**
- **FFmpeg** on system PATH

### Installation

```bash
git clone https://github.com/bradfordfrazier/HarvickFarmsVideoEditor.git
cd HarvickFarmsVideoEditor

# Install dependencies
pip install -r requirements.txt
```

### Running the Studio

```bash
python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000) in your browser.

---

## 🧪 Running Automated Tests

```bash
pytest tests/test_pipeline.py -v
```

## 📦 Windows Packaging & Distribution

To package the studio into a standalone Windows app and installer wizard that can be distributed to end users without needing Python or FFmpeg installed:

### 1-Click Build

Double-click `build_windows.bat` or run:

```bash
python scripts/build_package.py
```

### Outputs Generated

1. **Windows Installer Wizard**:
   - Location: `installer_output/HarvickFarmsVideoStudio_Setup_v1.0.0.exe` (169 MB)
   - Standard Windows setup wizard with Desktop icon, Start Menu shortcut, and uninstaller.
   - Fully standalone: embeds Python, FastAPI, UI assets, and static **FFmpeg & FFprobe** binaries.
   
2. **Portable Standalone App**:
   - Location: `dist/HarvickFarmsVideoStudio/HarvickFarmsVideoStudio.exe`
   - Can be zipped and copied directly to any Windows 10/11 computer to run without installation.

---

## 📄 License

Internal tool built for Harvick Farms. All rights reserved.
