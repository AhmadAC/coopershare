
"""
Persistence, image generation, low-level socket utilities, SVG vector icon renderers,
and KDE Plasma Wayland & uinput permission helpers.
"""

import json
import os
import shutil
import socket
import subprocess
import sys
from typing import Optional

import mss
from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer


def get_app_directory() -> str:
    """Returns the base directory where the application is located."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


HISTORY_FILE_PATH = os.path.join(get_app_directory(), "history.json")


def load_history() -> dict:
    """Loads saved devices, settings, and last connected IP from history.json."""
    if os.path.exists(HISTORY_FILE_PATH):
        try:
            with open(HISTORY_FILE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                if not isinstance(data.get("devices"), dict):
                    data["devices"] = {}
                return data
        except Exception as e:
            print(f"[DEBUG Sender] Failed to read history.json: {e}")
    return {"last_ip": "", "devices": {}}


def save_history(history_data: dict):
    """Saves the history data dict to history.json."""
    try:
        with open(HISTORY_FILE_PATH, "w", encoding="utf-8") as f:
            json.dump(history_data, f, indent=4)
        print("[DEBUG Sender] Saved history.json successfully.")
    except Exception as e:
        print(f"[DEBUG Sender] Failed to write history.json: {e}")


def ensure_uinput_permissions():
    """
    Checks write access to /dev/uinput on Linux and installs a standard udev rule
    if permissions are missing.
    """
    if not sys.platform.startswith("linux"):
        return

    if os.path.exists("/dev/uinput") and os.access("/dev/uinput", os.W_OK):
        return

    rule_path = "/etc/udev/rules.d/99-uinput.rules"
    rule_content = 'KERNEL=="uinput", MODE="0660", TAG+="uaccess"\n'

    if not os.path.exists(rule_path):
        try:
            if os.geteuid() == 0:
                with open(rule_path, "w", encoding="utf-8") as f:
                    f.write(rule_content)
                subprocess.run(["udevadm", "control", "--reload-rules"], check=False)
                subprocess.run(["udevadm", "trigger"], check=False)
                print("[INFO] Automatically created uinput udev rule.")
        except Exception:
            pass


def ensure_kde_desktop_entry(force: bool = False):
    """
    Ensures environment overrides and desktop entries exist for KWin ScreenShot2
    D-Bus authorization under KDE Plasma 6 Wayland.
    """
    if not sys.platform.startswith("linux"):
        return

    # 1. Register systemd environment configuration for kwin_wayland
    env_dir = os.path.expanduser("~/.config/environment.d")
    os.makedirs(env_dir, exist_ok=True)
    kwin_conf = os.path.join(env_dir, "10-kwin-screencopy.conf")
    env_content = (
        "KWIN_SCREENSHOT_NO_PERMISSION_CHECKS=1\n"
        "KWIN_WAYLAND_NO_PERMISSION_CHECKS=1\n"
    )
    if not os.path.exists(kwin_conf) or force:
        try:
            with open(kwin_conf, "w", encoding="utf-8") as f:
                f.write(env_content)
            print(f"[INFO] Registered KWin session environment override: {kwin_conf}")
        except Exception as e:
            print(f"[DEBUG KDE Permissions] Failed writing environment config: {e}")

    # 2. Register application desktop entries
    apps_dir = os.path.expanduser("~/.local/share/applications")
    os.makedirs(apps_dir, exist_ok=True)

    app_dir = get_app_directory()
    sender_path = os.path.join(app_dir, "sender.py")
    icon_png = os.path.join(app_dir, "icon.png")
    icon_val = icon_png if os.path.exists(icon_png) else "video-display"

    proc_self_exe = ""
    try:
        proc_self_exe = os.path.realpath(f"/proc/{os.getpid()}/exe")
    except Exception:
        pass

    real_py = os.path.realpath(sys.executable)
    sys_py = sys.executable
    which_py3 = shutil.which("python3") or ""
    which_py = shutil.which("python") or ""

    candidates = []
    for p in [proc_self_exe, real_py, sys_py, which_py3, which_py]:
        if p and os.path.exists(p) and p not in candidates:
            candidates.append(p)

    entries = [
        (
            os.path.join(apps_dir, "mrcoopers-screenshare-sender.desktop"),
            f"""[Desktop Entry]
Version=1.0
Type=Application
Name=MrCoopersScreenShare Sender
Comment=Screen sharing and touch controller
Exec={sys_py} {sender_path}
Path={app_dir}
Icon={icon_val}
Terminal=false
StartupNotify=false
Categories=Utility;Network;
X-KDE-DBUS-Restricted-Interfaces=org.kde.kwin.Screenshot,org.kde.KWin.ScreenShot2
X-KDE-Wayland-Interfaces=org_kde_plasma_window_management,zkde_screencast_unstable_v1
""",
        )
    ]

    for idx, exe_path in enumerate(candidates):
        filename = f"mrcoopers-py-engine-{idx}.desktop"
        entries.append(
            (
                os.path.join(apps_dir, filename),
                f"""[Desktop Entry]
Version=1.0
Type=Application
Name=MrCoopersScreenShare Python Engine {idx}
Exec={exe_path}
Terminal=false
StartupNotify=false
Categories=Utility;
X-KDE-DBUS-Restricted-Interfaces=org.kde.kwin.Screenshot,org.kde.KWin.ScreenShot2
X-KDE-Wayland-Interfaces=org_kde_plasma_window_management,zkde_screencast_unstable_v1
""",
            )
        )

    updated = False
    for path, content in entries:
        needs_write = force or not os.path.exists(path)
        if not needs_write and os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    curr = f.read()
                if "X-KDE-DBUS-Restricted-Interfaces" not in curr:
                    needs_write = True
            except Exception:
                needs_write = True

        if needs_write:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(content)
                os.chmod(path, 0o755)
                updated = True
                print(f"[INFO] Registered KDE permission entry: {path}")
            except Exception as e:
                print(f"[DEBUG KDE Permissions] Failed writing {path}: {e}")

    if updated or force:
        for update_cmd in [
            ["kbuildsycoca6", "--noincremental"],
            ["kbuildsycoca5", "--noincremental"],
            ["update-desktop-database", apps_dir],
        ]:
            if shutil.which(update_cmd[0]):
                try:
                    subprocess.run(
                        update_cmd,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=3,
                    )
                    print(f"[INFO] Rebuilt desktop cache with {update_cmd[0]}")
                except Exception:
                    pass


def create_application_icon() -> QIcon:
    """Generates a high-DPI desktop and taskbar icon for MrCoopersScreenShare."""
    pix = QPixmap(64, 64)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing, True)

    painter.setBrush(QColor("#0078d4"))
    painter.setPen(Qt.NoPen)
    painter.drawRoundedRect(4, 4, 56, 56, 14, 14)

    painter.setBrush(QColor("#ffffff"))
    painter.drawRoundedRect(14, 15, 36, 24, 4, 4)

    painter.setBrush(QColor("#1a1e29"))
    painter.drawRect(18, 19, 28, 16)

    painter.setBrush(QColor("#ffffff"))
    painter.drawRect(29, 41, 6, 4)
    painter.drawRoundedRect(22, 45, 20, 3, 1, 1)

    painter.end()
    return QIcon(pix)


def svg_to_pixmap(svg_str: str, width: int = 16, height: int = 16, color: Optional[str] = "#ffffff") -> QPixmap:
    """Renders raw SVG vector markup directly onto a crisp transparent QPixmap."""
    if color:
        svg_str = svg_str.replace("currentColor", color)
    renderer = QSvgRenderer(QByteArray(svg_str.encode("utf-8")))
    pix = QPixmap(width, height)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing, True)
    renderer.render(painter)
    painter.end()
    return pix


def svg_to_icon(svg_str: str, size: int = 16, color: Optional[str] = "#ffffff") -> QIcon:
    """Generates a high-DPI QIcon from raw SVG markup."""
    pix = svg_to_pixmap(svg_str, size, size, color)
    return QIcon(pix)


# ===========================================================================
# Standard Professional SVG Vector Icons
# ===========================================================================

SVG_STATUS_DOT = """<svg viewBox="0 0 16 16"><circle cx="8" cy="8" r="5" fill="currentColor"/></svg>"""
SVG_CHEVRON_DOWN = """<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>"""
SVG_CLOSE = """<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>"""
SVG_PAUSE = """<svg viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="4" width="4" height="16" rx="1"/><rect x="14" y="4" width="4" height="16" rx="1"/></svg>"""
SVG_PLAY = """<svg viewBox="0 0 24 24" fill="currentColor"><polygon points="6 4 20 12 6 20 6 4"/></svg>"""
SVG_VOLUME_ON = """<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" fill="currentColor"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14M15.54 8.46a5 5 0 0 1 0 7.07"/></svg>"""
SVG_VOLUME_MUTE = """<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" fill="currentColor"/><line x1="23" y1="9" x2="17" y2="15"/><line x1="17" y1="9" x2="23" y2="15"/></svg>"""
SVG_SCREEN = """<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>"""
SVG_TIMER = """<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>"""
SVG_DISPLAY = """<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="14" rx="2"/><path d="M7 21h10"/></svg>"""
SVG_MAXIMIZE = """<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M8 3H5a2 2 0 0 0-2 2v3m18 0V5a2 2 0 0 0-2-2h-3m0 18h3a2 2 0 0 0 2-2v-3M3 16v3a2 2 0 0 0 2 2h3"/></svg>"""
SVG_RESTORE = """<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="7" y="7" width="14" height="14" rx="2"/><path d="M3 17V5a2 2 0 0 1 2-2h12"/></svg>"""
SVG_MINIMIZE = """<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="5" y1="12" x2="19" y2="12"/></svg>"""
SVG_TAG = """<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"/><line x1="7" y1="7" x2="7.01" y2="7"/></svg>"""
SVG_TRASH = """<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>"""
SVG_TOUCH = """<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2v10m-4-6l4-4 4 4"/><circle cx="12" cy="16" r="4"/></svg>"""
SVG_ROCKET = """<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4.5 16.5c-1.5 1.26-2 5-2 5s3.74-.5 5-2c.71-.84.7-2.13-.09-2.91a2.18 2.18 0 0 0-2.91-.09z"/><path d="M12 15l-3-3a22 22 0 0 1 2-3.95A12.88 12.88 0 0 1 22 2c0 2.72-.78 7.5-6 11a22.35 22.35 0 0 1-4 2z"/></svg>"""
SVG_FULLSCREEN = """<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 3 21 3 21 9"/><polyline points="9 21 3 21 3 15"/><polyline points="21 15 21 21 15 21"/><polyline points="3 9 3 3 9 3"/></svg>"""
SVG_EXIT_FULLSCREEN = """<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="4 14 10 14 10 20"/><polyline points="20 10 14 10 14 4"/><polyline points="14 14 20 14 20 20"/><polyline points="10 10 4 10 4 4"/></svg>"""
SVG_DISCONNECT = """<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/><line x1="2" y1="2" x2="22" y2="22"/></svg>"""
SVG_LOGOUT = """<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>"""
SVG_INFO = """<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>"""


def recv_exact(sock: socket.socket, count: int) -> Optional[bytes]:
    """Reads exactly `count` bytes from socket or returns None on error/disconnect."""
    buf = bytearray()
    while len(buf) < count:
        try:
            chunk = sock.recv(count - len(buf))
            if not chunk:
                return None
            buf.extend(chunk)
        except (socket.timeout, BlockingIOError):
            continue
        except Exception:
            return None
    return bytes(buf)


def create_mss_instance():
    """Returns an mss instance without deprecation warnings."""
    if hasattr(mss, "MSS"):
        return mss.MSS()
    return mss.mss()
