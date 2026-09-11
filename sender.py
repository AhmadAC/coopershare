"""
MrCoopersScreenShare - Sender (PC Presenter & Remote Controller)
Main executable launcher and bootstrap script.
"""

import ctypes
import os
import runpy
import sys

from PySide6.QtWidgets import QApplication

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
            print(f"[BOOTSTRAP ERROR] Failed to run external sender.py: {_ex}")

from ui_main import FloatingSenderWindow
from utils import create_application_icon

if __name__ == "__main__":
    # Ensure proper Windows Taskbar Grouping & Icon
    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "mrcoopers.screenshare.sender.1"
            )
        except Exception:
            pass

    app = QApplication(sys.argv)
    app.setWindowIcon(create_application_icon())

    win = FloatingSenderWindow()
    win.show()
    win.move(80, 80)
    sys.exit(app.exec())