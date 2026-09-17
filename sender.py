#################### START OF FILE: sender.py ####################

"""
MrCoopersScreenShare - Sender (PC Presenter & Remote Controller)
Main executable launcher and bootstrap script with startup telemetry and high-resolution timer support.
"""

import ctypes
import os
import runpy
import sys

# Linux-Specific Platform & Wayland/Portal Environment Configuration
if sys.platform.startswith("linux"):
    os.environ["QT_QPA_PLATFORMTHEME"] = "xdgdesktopportal"
    os.environ["QT_IM_MODULE"] = "ibus"
    os.environ["XMODIFIERS"] = "@im=ibus"
    if "QT_QPA_PLATFORM" not in os.environ:
        os.environ["QT_QPA_PLATFORM"] = "wayland;xcb"

# Enable Windows 1ms high-precision scheduling for the entire process lifetime
if sys.platform == "win32":
    try:
        ctypes.windll.winmm.timeBeginPeriod(1)
    except Exception:
        pass

print("=" * 70, flush=True)
print(f"[Sender-Bootstrap] MrCoopersScreenShare Sender Starting...", flush=True)
print(f"[Sender-Bootstrap] Python: {sys.version.split()[0]} ({sys.executable})", flush=True)
print(f"[Sender-Bootstrap] Platform: {sys.platform}", flush=True)
print("=" * 70, flush=True)

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

# Register Desktop File Name early before QApplication instance creates Wayland/portal connection
if sys.platform.startswith("linux"):
    QGuiApplication.setDesktopFileName("mrcoopers-screenshare-sender")
    QApplication.setApplicationName("mrcoopers-screenshare-sender")

# ---------------------------------------------------------------------------
# Onedir Dynamic Script Loader (Allows replacing sender.py without recompiling)
# ---------------------------------------------------------------------------
if getattr(sys, "frozen", False) and os.environ.get("_MRCOOPERS_BOOTSTRAP") != "1":
    _app_dir = os.path.dirname(os.path.abspath(sys.executable))
    _external_script = os.path.join(_app_dir, "sender.py")
    if os.path.exists(_external_script):
        try:
            os.environ["_MRCOOPERS_BOOTSTRAP"] = "1"
            runpy.run_path(_external_script, run_name="__main__")
            sys.exit(0)
        except SystemExit:
            raise
        except Exception as _ex:
            print(f"[BOOTSTRAP ERROR] Failed to run external sender.py: {_ex}", flush=True)

from ui_main import FloatingSenderWindow
from utils import (
    WindowsCaptureExclusionFilter,
    create_application_icon,
    ensure_kde_desktop_entry,
    ensure_uinput_permissions,
)

# Ensure KDE screen capture & Linux kernel touch permissions exist
ensure_kde_desktop_entry()
ensure_uinput_permissions()

if __name__ == "__main__":
    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "mrcoopers.screenshare.sender.1"
            )
        except Exception:
            pass

    app = QApplication(sys.argv)
    app.setWindowIcon(create_application_icon())

    if sys.platform == "win32":
        exclusion_filter = WindowsCaptureExclusionFilter(app)
        app.installEventFilter(exclusion_filter)

    win = FloatingSenderWindow()
    win.show()
    win.move(80, 80)
    print("[Sender-Bootstrap] GUI initialized and ready. Waiting for connection...", flush=True)

    exit_code = app.exec()

    if sys.platform == "win32":
        try:
            ctypes.windll.winmm.timeEndPeriod(1)
        except Exception:
            pass

    sys.exit(exit_code)