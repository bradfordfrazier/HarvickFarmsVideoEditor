"""
Harvick Farms Video Studio - Desktop Application Launcher
Handles Uvicorn server lifecycle and native desktop window rendering via pywebview.
"""

import sys
import os
import io
import time
import socket
import threading
import webbrowser
import argparse
from pathlib import Path


class SafeStream(io.TextIOBase):
    """
    Drop-in replacement for sys.stdout / sys.stderr in windowed (GUI) mode.
    Guarantees standard stream attributes (e.g. isatty -> False) and safely
    redirects text output to an optional persistent log file.
    """

    def __init__(self, target_file=None):
        self.target_file = target_file

    def write(self, s):
        if self.target_file and s:
            try:
                self.target_file.write(s)
                self.target_file.flush()
            except Exception:
                pass
        return len(s) if s else 0

    def writelines(self, lines):
        for line in lines:
            self.write(line)

    def flush(self):
        if self.target_file:
            try:
                self.target_file.flush()
            except Exception:
                pass

    def isatty(self):
        return False

    def readable(self):
        return False

    def writable(self):
        return True

    def seekable(self):
        return False

    @property
    def encoding(self):
        return "utf-8"

    @property
    def errors(self):
        return "replace"


def setup_gui_environment():
    """Ensure standard I/O streams are valid in windowed / frozen GUI environments."""
    log_file = None
    needs_patch = (
        sys.stdout is None
        or sys.stderr is None
        or not hasattr(sys.stdout, "isatty")
        or not hasattr(sys.stderr, "isatty")
    )

    if needs_patch:
        try:
            log_dir = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "HarvickFarmsVideoEditor"
            log_dir.mkdir(parents=True, exist_ok=True)
            log_file = open(log_dir / "studio.log", "a", encoding="utf-8", errors="replace")
        except Exception:
            try:
                import tempfile
                log_file = open(Path(tempfile.gettempdir()) / "harvick_studio.log", "a", encoding="utf-8", errors="replace")
            except Exception:
                log_file = None

    if sys.stdout is None or not hasattr(sys.stdout, "isatty"):
        sys.stdout = SafeStream(log_file)

    if sys.stderr is None or not hasattr(sys.stderr, "isatty"):
        sys.stderr = SafeStream(log_file)

    if sys.stdin is None:
        sys.stdin = io.StringIO()


# Initialize safe streams immediately before importing server and GUI libraries
setup_gui_environment()

# Add project root to sys.path so modules resolve correctly in frozen and non-frozen mode
BASE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from backend.config import BASE_DIR, ASSETS_DIR, FFMPEG_PATH, FFPROBE_PATH
from backend.app import app
import uvicorn


def get_free_port(default_port: int = 8000) -> int:
    """Check if default_port is free, otherwise find an available dynamic port."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", default_port))
            return default_port
    except OSError:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]


def wait_for_server(port: int, timeout: float = 8.0) -> bool:
    """Wait for the local HTTP server to become responsive."""
    import urllib.request
    start = time.time()
    url = f"http://127.0.0.1:{port}/"
    while time.time() - start < timeout:
        try:
            with urllib.request.urlopen(url, timeout=0.5) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.1)
    return False


def run_desktop_app():
    parser = argparse.ArgumentParser(description="Harvick Farms Video Studio")
    parser.add_argument("--port", type=int, default=8000, help="HTTP server port")
    parser.add_argument("--server-only", action="store_true", help="Run HTTP server only without desktop window")
    parser.add_argument("--browser", action="store_true", help="Launch in default web browser instead of native window")
    args, unknown = parser.parse_known_args()

    port = get_free_port(args.port)
    server_url = f"http://127.0.0.1:{port}"

    # Configure Uvicorn server instance
    config = uvicorn.Config(
        app=app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        access_log=False,
        use_colors=False
    )
    server = uvicorn.Server(config)

    # Start server in a background daemon thread
    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()

    # Wait for the server to spin up
    server_ready = wait_for_server(port)

    if args.server_only:
        print(f"Harvick Farms Video Studio running at: {server_url}")
        try:
            while server_thread.is_alive():
                time.sleep(1)
        except KeyboardInterrupt:
            server.should_exit = True
        return

    # Try launching native desktop window with pywebview
    use_webview = not args.browser
    if use_webview:
        try:
            import webview
            
            # Verify icon exists
            icon_path = ASSETS_DIR / "app_icon.ico"
            if not icon_path.exists():
                icon_path = ASSETS_DIR / "harvick_farms_logo.png"

            window = webview.create_window(
                title="Harvick Farms Video Studio 🎬🌾",
                url=server_url,
                width=1420,
                height=900,
                min_size=(1080, 720),
                confirm_close=False,
                background_color="#0F141C"
            )

            # Start webview loop (blocks until the window is closed)
            webview.start(icon=str(icon_path) if icon_path.exists() else None)
            
            # User closed window: signal server shutdown
            server.should_exit = True
            return

        except Exception as e:
            print(f"Desktop window initialization failed ({e}), falling back to browser mode.")

    # Fallback to browser
    webbrowser.open(server_url)
    print(f"Studio running in browser at {server_url}. Press Ctrl+C to stop.")
    try:
        while server_thread.is_alive():
            time.sleep(1)
    except KeyboardInterrupt:
        server.should_exit = True


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    try:
        run_desktop_app()
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        try:
            sys.stderr.write(f"\nFATAL UNCAUGHT ERROR:\n{tb}\n")
            sys.stderr.flush()
        except Exception:
            pass
        if getattr(sys, "frozen", False):
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(
                    0,
                    f"Harvick Farms Video Studio encountered an error during startup:\n\n{e}\n\nPlease check the log file in %LOCALAPPDATA%\\HarvickFarmsVideoEditor\\studio.log.",
                    "Harvick Farms Video Studio - Startup Error",
                    0x10,
                )
            except Exception:
                pass
        sys.exit(1)
