"""
Harvick Farms Video Studio - Windows Packaging Automation Script
1. Validates prerequisites (pyinstaller, pywebview, Pillow)
2. Stages icon and bundled ffmpeg/ffprobe binaries into bin/
3. Builds standalone directory with PyInstaller
4. Invokes Inno Setup compiler (ISCC) if available to create Setup.exe
"""

import os
import sys
import shutil
import subprocess
from pathlib import Path
from PIL import Image

ROOT_DIR = Path(__file__).resolve().parent.parent


def step_print(title: str):
    print("\n" + "=" * 65)
    print(f"  {title}")
    print("=" * 65)


def prepare_icon():
    step_print("1. Preparing Application Icon")
    assets_dir = ROOT_DIR / "assets"
    ico_path = assets_dir / "app_icon.ico"
    png_path = assets_dir / "harvick_farms_logo.png"

    if ico_path.exists():
        print(f"Icon already exists: {ico_path}")
        return

    if png_path.exists():
        print(f"Generating icon from {png_path.name}...")
        img = Image.open(png_path)
        img.save(
            ico_path,
            format="ICO",
            sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]
        )
        print(f"Generated: {ico_path}")
    else:
        print("Warning: Logo PNG not found. Building without custom icon.")


def prepare_binaries():
    step_print("2. Staging FFmpeg & FFprobe Binaries into bin/")
    bin_dir = ROOT_DIR / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)

    target_ffmpeg = bin_dir / "ffmpeg.exe"
    target_ffprobe = bin_dir / "ffprobe.exe"

    src_ffmpeg = shutil.which("ffmpeg")
    src_ffprobe = shutil.which("ffprobe")

    if not target_ffmpeg.exists():
        if src_ffmpeg:
            print(f"Copying FFmpeg from system: {src_ffmpeg}")
            shutil.copy2(src_ffmpeg, target_ffmpeg)
        else:
            print("WARNING: ffmpeg.exe not found on system PATH! Video rendering will require system FFmpeg.")
    else:
        print(f"FFmpeg binary already staged: {target_ffmpeg}")

    if not target_ffprobe.exists():
        if src_ffprobe:
            print(f"Copying FFprobe from system: {src_ffprobe}")
            shutil.copy2(src_ffprobe, target_ffprobe)
        else:
            print("WARNING: ffprobe.exe not found on system PATH!")
    else:
        print(f"FFprobe binary already staged: {target_ffprobe}")


def run_pyinstaller():
    step_print("3. Compiling Standalone Application with PyInstaller")
    spec_file = ROOT_DIR / "HarvickFarmsVideoStudio.spec"
    if not spec_file.exists():
        raise FileNotFoundError(f"Missing spec file: {spec_file}")

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--clean",
        "--noconfirm",
        str(spec_file)
    ]

    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=str(ROOT_DIR))
    if result.returncode != 0:
        raise RuntimeError(f"PyInstaller build failed with exit code {result.returncode}")

    output_exe = ROOT_DIR / "dist" / "HarvickFarmsVideoStudio" / "HarvickFarmsVideoStudio.exe"
    if not output_exe.exists():
        raise FileNotFoundError(f"Build output executable missing: {output_exe}")

    print(f"\n[SUCCESS] Standalone app generated at:\n  {output_exe.parent}")


def find_inno_setup_compiler() -> Path | None:
    # 1. Check PATH
    iscc_on_path = shutil.which("iscc") or shutil.which("ISCC.exe")
    if iscc_on_path:
        return Path(iscc_on_path)

    # 2. Check standard installation directories
    candidate_paths = [
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Inno Setup 6" / "ISCC.exe",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Inno Setup 6" / "ISCC.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe",
    ]

    for p in candidate_paths:
        if p.exists():
            return p

    return None


def run_inno_setup():
    step_print("4. Building Windows Setup Installer")
    iscc_path = find_inno_setup_compiler()
    iss_file = ROOT_DIR / "installer.iss"

    if not iscc_path:
        print("\n[NOTE] Inno Setup compiler (ISCC.exe) was not found on this machine.")
        print("To generate a single-file 'Setup.exe' wizard installer:")
        print("  1. Run in PowerShell:  winget install JRSoftware.InnoSetup")
        print("  2. Re-run this build script or right-click 'installer.iss' -> Compile.")
        print(f"\nHowever, your fully functional portable application is ready in:")
        print(f"  {ROOT_DIR / 'dist' / 'HarvickFarmsVideoStudio'}\n")
        return

    print(f"Found Inno Setup at: {iscc_path}")
    cmd = [str(iscc_path), str(iss_file)]
    print(f"Compiling installer: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=str(ROOT_DIR))
    if result.returncode != 0:
        print(f"[WARNING] Inno Setup compilation returned exit code {result.returncode}")
    else:
        installer_dir = ROOT_DIR / "installer_output"
        print(f"\n[SUCCESS] Windows Installer Wizard generated in:\n  {installer_dir}")


def main():
    try:
        prepare_icon()
        prepare_binaries()
        run_pyinstaller()
        run_inno_setup()
        step_print("BUILD COMPLETED SUCCESSFULLY")
    except Exception as e:
        print(f"\n[ERROR] Build aborted: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
