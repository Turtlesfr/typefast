"""Windows counterparts of x11.py and xkeys.py, with the same classes and methods."""

import ctypes
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)

# Stamped on every event TypeFast injects, so the listener can skip its own keys
# (the X11 version skips the XTEST device instead).
MARKER = 0x54464B59  # "TFKY"
