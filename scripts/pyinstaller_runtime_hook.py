"""
PyInstaller Runtime Hook for Harvick Farms Video Studio.
Executed at application launch before any user modules are imported.
Ensures sys.stdin, sys.stdout, and sys.stderr are valid stream objects when running in windowed (GUI) mode.
"""

import sys
import io


class NullStream(io.TextIOBase):
    """Safe fallback stream for GUI mode where console streams are None."""

    def write(self, s):
        return len(s) if s else 0

    def writelines(self, lines):
        pass

    def flush(self):
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


# In Windows GUI executables (console=False), standard streams are None.
# Patch them so logging, uvicorn, and library formatters do not crash with AttributeError.
if sys.stdout is None or not hasattr(sys.stdout, "isatty"):
    sys.stdout = NullStream()

if sys.stderr is None or not hasattr(sys.stderr, "isatty"):
    sys.stderr = NullStream()

if sys.stdin is None:
    sys.stdin = io.StringIO()
