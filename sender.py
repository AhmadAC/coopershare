#################### START OF FILE: sender.py ####################

"""
MrCoopersScreenShare - Sender (PC Presenter & Remote Controller)
Main executable launcher and bootstrap script.
"""

import ctypes
import os
import runpy
import subprocess
import sys

# Linux-Specific Platform & KWin Permissions for Fedora Wayland / KDE Plasma
if sys.platform.startswith("linux"):
    os.environ["KWIN_SCREENSHOT_NO_PERMISSION_CHECKS"] = "1"
    os.environ["QT_QPA_PLATFORMTHEME"] = "xdgdesktopportal"
    os.environ["QT_IM_MODULE"] = "ibus"
    os.environ["XMODIFIERS"] = "@im=ibus"
    if "QT_QPA_PLATFORM" not in os.environ:
        os.environ["QT_QPA_PLATFORM"] = "wayland;xcb"


def ensure_kde_desktop_entry():
    """Ensures a desktop entry exists with KWin ScreenShot2 D-Bus permissions and reloads KDE cache."""
    if not sys.platform.startswith("linux"):
        return
    apps_dir = os.path.expanduser("~/.local/share/applications")
    os.makedirs(apps_dir, exist_ok=True)
    target_path = os.path.join(apps_dir, "mrcoopers-screenshare-sender.desktop")

    entry_content = f"""[Desktop Entry]
Version=1.0
Type=Application
Name=MrCoopersScreenShare Sender
Comment=MrCoopersScreenShare Sender
Exec={sys.executable} "{os.path.abspath(__file__)}"
Path={os.path.dirname(os.path.abspath(__file__))}
Icon=video-display
Terminal=false
StartupNotify=true
Categories=Utility;Network;
X-KDE-DBUS-Restricted-Interfaces=org.kde.kwin.Screenshot,org.kde.KWin.ScreenShot2
"""
    updated = False
    try:
        if not os.path.exists(target_path):
            with open(target_path, "w", encoding="utf-8") as f:
                f.write(entry_content)
            os.chmod(target_path, 0o755)
            updated = True
        else:
            with open(target_path, "r", encoding="utf-8") as f:
                curr = f.read()
            if "X-KDE-DBUS-Restricted-Interfaces" not in curr:
                with open(target_path, "w", encoding="utf-8") as f:
                    f.write(entry_content)
                os.chmod(target_path, 0o755)
                updated = True

        if updated:
            for update_cmd in [
                ["kbuildsycoca6"],
                ["kbuildsycoca5"],
                ["update-desktop-database", apps_dir],
            ]:
                try:
                    subprocess.run(
                        update_cmd,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=2,
                    )
                except Exception:
                    pass
    except Exception as e:
        print(f"[DEBUG Sender] Desktop entry check notice: {e}")


# Enable Per-Monitor High DPI Awareness on Windows early
if sys.platform == "win32":
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

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

ensure_kde_desktop_entry()

from ui_main import FloatingSenderWindow
from utils import create_application_icon

if __name__ == "__main__":
    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "mrcoopers.screenshare.sender.1"
            )
        except Exception:
            pass

    app = QApplication(sys.argv)
    if sys.platform.startswith("linux"):
        app.setDesktopFileName("mrcoopers-screenshare-sender")
        app.setApplicationName("mrcoopers-screenshare-sender")

    app.setWindowIcon(create_application_icon())

    win = FloatingSenderWindow()
    win.show()
    win.move(80, 80)
    sys.exit(app.exec())