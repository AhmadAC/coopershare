"""
MrCoopersScreenShare - Receiver (Interactive Touch Display & Sound Hub)
Features: Fullscreen Frameless Mode, Local Script / Executable Launcher with JSON History Memory,
          Local & Remote Timer Launcher with Robust sys.argv Argument Passing (Raw Numbers / Unit Strings),
          Reverse Desktop Screen Streaming & Interactive Remote Input (Mouse + Keyboard / Hotkeys),
          Remote Window Management (Maximize, Normal, Minimize),
          Right-Click Context Menu (Run Script, Show Timer, Fullscreen, Standby Details Visibility),
          Dynamic Audio Playback, Native Win32 / Universal Input Injection (evdev/pynput),
          UDP Discovery Beacon, 4-Digit PIN Authentication,
          Vector SVG Icons replacing emojis,
          Hardware Multi-Touch QTouchEvent Processing.
"""

import ctypes
from ctypes import Structure, byref, c_int, c_long, sizeof
import json
import logging
import os
import random
import runpy
import shlex
import socket
import struct
import subprocess
import sys
import threading
import time
from typing import Optional

# Prevent standard streams crash when executed without a console window on Windows
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

# ---------------------------------------------------------------------------
# Storage Directory Resolution (Handles AppImage, Frozen Onedir, & Fallbacks)
# ---------------------------------------------------------------------------
def get_storage_directory() -> str:
    appimage = os.environ.get("APPIMAGE")
    if appimage:
        candidate = os.path.dirname(os.path.abspath(appimage))
        if os.path.isdir(candidate) and os.access(candidate, os.W_OK):
            return candidate

    if getattr(sys, "frozen", False):
        candidate = os.path.dirname(os.path.abspath(sys.executable))
    else:
        candidate = os.path.dirname(os.path.abspath(__file__))

    if os.path.isdir(candidate) and os.access(candidate, os.W_OK):
        return candidate

    fallback = os.path.expanduser("~/.config/mrcoopers-screenshare")
    os.makedirs(fallback, exist_ok=True)
    return fallback


APP_DIR = get_storage_directory()
LOG_FILE_PATH = os.path.join(APP_DIR, "mrcoopers_receiver.log")
CONFIG_FILE_PATH = os.path.join(APP_DIR, "receiver_config.json")

# ---------------------------------------------------------------------------
# Onedir Dynamic Script Loader
# ---------------------------------------------------------------------------
if getattr(sys, "frozen", False) and os.environ.get("_MRCOOPERS_BOOTSTRAP_REC") != "1":
    _app_dir = os.path.dirname(os.path.abspath(sys.executable))
    _external_script = os.path.join(_app_dir, "receiver.py")
    if os.path.exists(_external_script):
        try:
            os.environ["_MRCOOPERS_BOOTSTRAP_REC"] = "1"
            runpy.run_path(_external_script, run_name="__main__")
            sys.exit(0)
        except SystemExit:
            raise
        except Exception as _ex:
            print(f"[BOOTSTRAP ERROR] Failed to run external receiver.py: {_ex}")

import cv2
import mss
import numpy as np
from PySide6.QtCore import QByteArray, QEvent, QPoint, QPointF, QRect, Qt, QThread, Signal
from PySide6.QtGui import (
    QAction,
    QColor,
    QCursor,
    QFont,
    QIcon,
    QImage,
    QKeyEvent,
    QPainter,
    QPalette,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger("Receiver")
logger.setLevel(logging.DEBUG)

if not logger.handlers:
    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] [%(threadName)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler = logging.FileHandler(LOG_FILE_PATH, mode="w", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.DEBUG)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)


def create_application_icon() -> QIcon:
    """Generates the identical crisp application icon or loads existing .ico/.png."""
    for candidate in (os.path.join(APP_DIR, "icon.ico"), os.path.join(APP_DIR, "icon.png")):
        if os.path.exists(candidate):
            return QIcon(candidate)

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
    if color:
        svg_str = svg_str.replace("currentColor", color)
    try:
        from PySide6.QtSvg import QSvgRenderer
        renderer = QSvgRenderer(QByteArray(svg_str.encode("utf-8")))
        pix = QPixmap(width, height)
        pix.fill(Qt.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.Antialiasing, True)
        renderer.render(painter)
        painter.end()
        return pix
    except Exception:
        pix = QPixmap(width, height)
        pix.fill(Qt.transparent)
        return pix


def svg_to_icon(svg_str: str, size: int = 16, color: Optional[str] = "#ffffff") -> QIcon:
    pix = svg_to_pixmap(svg_str, size, size, color)
    return QIcon(pix)


# Clean Vector SVGs for Receiver
REC_SVG_ROCKET = """<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4.5 16.5c-1.5 1.26-2 5-2 5s3.74-.5 5-2c.71-.84.7-2.13-.09-2.91a2.18 2.18 0 0 0-2.91-.09z"/><path d="M12 15l-3-3a22 22 0 0 1 2-3.95A12.88 12.88 0 0 1 22 2c0 2.72-.78 7.5-6 11a22.35 22.35 0 0 1-4 2z"/></svg>"""
REC_SVG_TIMER = """<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>"""
REC_SVG_INFO = """<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>"""
REC_SVG_FULLSCREEN = """<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 3 21 3 21 9"/><polyline points="9 21 3 21 3 15"/><polyline points="21 15 21 21 15 21"/><polyline points="3 9 3 3 9 3"/></svg>"""
REC_SVG_EXIT_FULLSCREEN = """<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="4 14 10 14 10 20"/><polyline points="20 10 14 10 14 4"/><polyline points="14 14 20 14 20 20"/><polyline points="10 10 4 10 4 4"/></svg>"""
REC_SVG_DISCONNECT = """<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/><line x1="2" y1="2" x2="22" y2="22"/></svg>"""
REC_SVG_LOGOUT = """<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>"""


def load_receiver_config() -> dict:
    if os.path.exists(CONFIG_FILE_PATH):
        try:
            with open(CONFIG_FILE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                if not isinstance(data.get("recent_scripts"), list):
                    data["recent_scripts"] = []
                return data
        except Exception as e:
            logger.warning(f"Failed to read receiver_config.json: {e}")
    return {
        "hide_details": False,
        "last_script_path": "",
        "last_script_args": "",
        "last_timer_args": "",
        "recent_scripts": [],
    }


def save_receiver_config(config: dict):
    try:
        with open(CONFIG_FILE_PATH, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4)
    except Exception as e:
        logger.error(f"Failed to save receiver_config.json: {e}")


# Optional Sound Support
try:
    import sounddevice as sd

    AUDIO_AVAILABLE = True
except Exception as e:
    AUDIO_AVAILABLE = False
    logger.warning(f"sounddevice audio unavailable: {e}")

# Universal Input Injection (evdev / pynput)
USE_EVDEV = False
if sys.platform.startswith("linux"):
    try:
        import evdev
        from evdev import AbsInfo, UInput, ecodes as e

        USE_EVDEV = True
    except Exception:
        USE_EVDEV = False

Button = None
MouseController = None
KeyboardController = None
Key = None
try:
    from pynput.keyboard import Controller as KeyboardController, Key
    from pynput.mouse import Button, Controller as MouseController

    PYNPUT_AVAILABLE = True
except Exception as e:
    PYNPUT_AVAILABLE = False
    logger.warning(f"pynput input library unavailable: {e}")

VIDEO_PORT = 9988
CONTROL_PORT = 9989
AUDIO_PORT = 9990
DISCOVERY_PORT = 9991
REVERSE_VIDEO_PORT = 9992
DEFAULT_SAMPLE_RATE = 48000
CHANNELS = 2
SOCKET_BUFFER_SIZE = 2 * 1024 * 1024


def get_local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "127.0.0.1"
    finally:
        s.close()


def recv_exact(sock: socket.socket, count: int) -> Optional[bytes]:
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
    if hasattr(mss, "MSS"):
        return mss.MSS()
    return mss.mss()


# ---------------------------------------------------------------------------
# Mouse Cursor Overlay
# ---------------------------------------------------------------------------


class POINT(Structure):
    _fields_ = [("x", c_long), ("y", c_long)]


def get_system_cursor_position() -> tuple[int, int]:
    if sys.platform == "win32":
        try:
            pt = POINT()
            ctypes.windll.user32.GetCursorPos(byref(pt))
            return int(pt.x), int(pt.y)
        except Exception:
            pass
    pos = QCursor.pos()
    return pos.x(), pos.y()


def render_cursor_on_frame(bgr_image: np.ndarray, monitor_left: int, monitor_top: int):
    gx, gy = get_system_cursor_position()
    cx = gx - monitor_left
    cy = gy - monitor_top

    h, w, _ = bgr_image.shape
    if 0 <= cx < w and 0 <= cy < h:
        pts = np.array(
            [
                [cx, cy],
                [cx, cy + 18],
                [cx + 4, cy + 14],
                [cx + 8, cy + 22],
                [cx + 11, cy + 21],
                [cx + 7, cy + 13],
                [cx + 14, cy + 13],
            ],
            np.int32,
        )
        cv2.polylines(bgr_image, [pts], isClosed=True, color=(0, 0, 0), thickness=2, lineType=cv2.LINE_AA)
        cv2.fillPoly(bgr_image, [pts], color=(255, 255, 255), lineType=cv2.LINE_AA)
        cv2.polylines(bgr_image, [pts], isClosed=True, color=(20, 20, 20), thickness=1, lineType=cv2.LINE_AA)


# ---------------------------------------------------------------------------
# Universal Input Injector (Direct Win32 API + evdev Wayland / pynput Fallback)
# ---------------------------------------------------------------------------


class UniversalInputInjector:
    MOUSEEVENTF_LEFTDOWN = 0x0002
    MOUSEEVENTF_LEFTUP = 0x0004
    MOUSEEVENTF_RIGHTDOWN = 0x0008
    MOUSEEVENTF_RIGHTUP = 0x0010
    MOUSEEVENTF_MIDDLEDOWN = 0x0020
    MOUSEEVENTF_MIDDLEUP = 0x0040
    MOUSEEVENTF_WHEEL = 0x0800
    KEYEVENTF_EXTENDEDKEY = 0x0001
    KEYEVENTF_KEYUP = 0x0002

    QT_KEY_TO_VK = {
        0x01000000: 0x1B,
        0x01000001: 0x09,
        0x01000002: 0x09,
        0x01000003: 0x08,
        0x01000004: 0x0D,
        0x01000005: 0x0D,
        0x01000006: 0x2D,
        0x01000007: 0x2E,
        0x01000008: 0x13,
        0x01000009: 0x2A,
        0x01000010: 0x24,
        0x01000011: 0x23,
        0x01000012: 0x25,
        0x01000013: 0x26,
        0x01000014: 0x27,
        0x01000015: 0x28,
        0x01000016: 0x21,
        0x01000017: 0x22,
        0x01000020: 0x10,
        0x01000021: 0x11,
        0x01000022: 0x5B,
        0x01000023: 0x12,
        0x01000024: 0x14,
        0x01000025: 0x90,
        0x01000026: 0x91,
        0x20: 0x20,
    }

    for _i in range(1, 25):
        QT_KEY_TO_VK[0x01000030 + _i - 1] = 0x70 + _i - 1

    NAME_TO_VK = {
        "return": 0x0D,
        "enter": 0x0D,
        "backspace": 0x08,
        "tab": 0x09,
        "escape": 0x1B,
        "space": 0x20,
        "delete": 0x2E,
        "shift": 0x10,
        "control": 0x11,
        "ctrl": 0x11,
        "alt": 0x12,
        "meta": 0x5B,
        "up": 0x26,
        "down": 0x28,
        "left": 0x25,
        "right": 0x27,
        "home": 0x24,
        "end": 0x23,
        "pageup": 0x21,
        "page_up": 0x21,
        "pagedown": 0x22,
        "page_down": 0x22,
    }

    def __init__(self, mon_left: int, mon_top: int, screen_w: int, screen_h: int):
        self.mon_left = mon_left
        self.mon_top = mon_top
        self.screen_w = max(1, screen_w)
        self.screen_h = max(1, screen_h)
        self.mode = "none"
        self.mouse = None
        self.keyboard = None
        self.ui = None

        if sys.platform == "win32":
            self.mode = "win32"
            logger.debug(f"Using native Win32 hardware input injection on bounds ({mon_left},{mon_top},{screen_w}x{screen_h}).")
        elif USE_EVDEV:
            try:
                min_x = min(0, mon_left)
                max_x = max(self.screen_w, mon_left + self.screen_w)
                min_y = min(0, mon_top)
                max_y = max(self.screen_h, mon_top + self.screen_h)

                cap = {
                    e.EV_KEY: [
                        e.BTN_LEFT,
                        e.BTN_RIGHT,
                        e.BTN_MIDDLE,
                        e.BTN_TOUCH,
                        e.BTN_TOOL_FINGER,
                    ],
                    e.EV_ABS: [
                        (e.ABS_X, AbsInfo(value=0, min=min_x, max=max_x, fuzz=0, flat=0, resolution=1)),
                        (e.ABS_Y, AbsInfo(value=0, min=min_y, max=max_y, fuzz=0, flat=0, resolution=1)),
                    ],
                    e.EV_REL: [e.REL_WHEEL],
                }

                self.ui = UInput(cap, name="mrcoopers-receiver-input")
                self.mode = "evdev"
                logger.debug(f"Linux evdev virtual pointer active ({max_x}x{max_y}).")
            except Exception as ex:
                logger.debug(f"Linux evdev init notice: {ex}")
                self.mode = "none"

        if self.mode == "none":
            if PYNPUT_AVAILABLE:
                try:
                    self.mouse = MouseController()
                    self.keyboard = KeyboardController()
                    self.mode = "pynput"
                    logger.debug("Using pynput fallback.")
                except Exception as ex:
                    logger.debug(f"pynput init failed: {ex}")
                    self.mode = "unsupported"
            else:
                self.mode = "unsupported"

    def execute(self, event: dict):
        ev_type = event.get("type")
        nx = event.get("x")
        ny = event.get("y")

        if nx is not None and ny is not None:
            px = self.mon_left + int(np.clip(nx, 0.0, 1.0) * (self.screen_w - 1))
            py = self.mon_top + int(np.clip(ny, 0.0, 1.0) * (self.screen_h - 1))
        else:
            px, py = None, None

        if self.mode == "win32":
            try:
                if px is not None and py is not None:
                    ctypes.windll.user32.SetCursorPos(int(px), int(py))

                if ev_type in ("touch_down", "mouse_down"):
                    btn = event.get("button", "left")
                    if btn == "right":
                        ctypes.windll.user32.mouse_event(self.MOUSEEVENTF_RIGHTDOWN, 0, 0, 0, 0)
                    elif btn == "middle":
                        ctypes.windll.user32.mouse_event(self.MOUSEEVENTF_MIDDLEDOWN, 0, 0, 0, 0)
                    else:
                        ctypes.windll.user32.mouse_event(self.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)

                elif ev_type in ("touch_up", "mouse_up"):
                    btn = event.get("button", "left")
                    if btn == "right":
                        ctypes.windll.user32.mouse_event(self.MOUSEEVENTF_RIGHTUP, 0, 0, 0, 0)
                    elif btn == "middle":
                        ctypes.windll.user32.mouse_event(self.MOUSEEVENTF_MIDDLEUP, 0, 0, 0, 0)
                    else:
                        ctypes.windll.user32.mouse_event(self.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)

                elif ev_type == "scroll":
                    dy = event.get("dy", 0)
                    delta = 120 if dy > 0 else -120
                    ctypes.windll.user32.mouse_event(self.MOUSEEVENTF_WHEEL, 0, 0, delta, 0)

                elif ev_type in ("key_down", "key_up"):
                    key_code = event.get("key_code", 0)
                    key_name = str(event.get("key", "")).lower().replace("key_", "")
                    text = event.get("text", "")

                    vk = self.QT_KEY_TO_VK.get(key_code)
                    if not vk:
                        vk = self.NAME_TO_VK.get(key_name)

                    if not vk and 0x20 <= key_code <= 0x7E:
                        vk = key_code

                    if not vk and text:
                        vk_scan = ctypes.windll.user32.VkKeyScanW(ord(text[0]))
                        if vk_scan != -1:
                            vk = vk_scan & 0xFF

                    if vk:
                        flags = 0 if ev_type == "key_down" else self.KEYEVENTF_KEYUP
                        if vk in (0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E):
                            flags |= self.KEYEVENTF_EXTENDEDKEY
                        ctypes.windll.user32.keybd_event(vk, 0, flags, 0)
                return
            except Exception as ex:
                logger.error(f"Win32 input injection exception: {ex}")

        elif self.mode == "evdev" and self.ui:
            try:
                if px is not None and py is not None:
                    self.ui.write(e.EV_ABS, e.ABS_X, int(px))
                    self.ui.write(e.EV_ABS, e.ABS_Y, int(py))

                if ev_type in ("touch_down", "mouse_down"):
                    btn_type = event.get("button", "left")
                    btn_code = (
                        e.BTN_RIGHT
                        if btn_type == "right"
                        else (e.BTN_MIDDLE if btn_type == "middle" else e.BTN_LEFT)
                    )
                    if btn_code == e.BTN_LEFT:
                        self.ui.write(e.EV_KEY, e.BTN_TOUCH, 1)
                    self.ui.write(e.EV_KEY, btn_code, 1)
                    self.ui.syn()

                elif ev_type in ("touch_up", "mouse_up"):
                    btn_type = event.get("button", "left")
                    btn_code = (
                        e.BTN_RIGHT
                        if btn_type == "right"
                        else (e.BTN_MIDDLE if btn_type == "middle" else e.BTN_LEFT)
                    )
                    if btn_code == e.BTN_LEFT:
                        self.ui.write(e.EV_KEY, e.BTN_TOUCH, 0)
                    self.ui.write(e.EV_KEY, btn_code, 0)
                    self.ui.syn()

                elif ev_type in ("touch_move", "mouse_move"):
                    self.ui.syn()

                elif ev_type == "scroll":
                    dy = 1 if event.get("dy", 0) > 0 else -1
                    self.ui.write(e.EV_REL, e.REL_WHEEL, dy)
                    self.ui.syn()
                return
            except Exception as ex:
                logger.error(f"evdev input injection exception: {ex}")

        # Linux / pynput Fallback
        if self.mouse:
            if px is not None and py is not None:
                self.mouse.position = (px, py)

            if ev_type in ("touch_down", "mouse_down"):
                btn = Button.right if event.get("button") == "right" else (
                    Button.middle if event.get("button") == "middle" else Button.left
                )
                self.mouse.press(btn)

            elif ev_type in ("touch_up", "mouse_up"):
                btn = Button.right if event.get("button") == "right" else (
                    Button.middle if event.get("button") == "middle" else Button.left
                )
                self.mouse.release(btn)

            elif ev_type == "scroll":
                dy = event.get("dy", 0)
                self.mouse.scroll(0, 1 if dy > 0 else -1)

        if self.keyboard and ev_type in ("key_down", "key_up"):
            text = event.get("text", "")
            key_name = str(event.get("key", "")).lower().replace("key_", "")
            target_key = text if text else key_name
            if target_key:
                try:
                    if ev_type == "key_down":
                        self.keyboard.press(target_key)
                    else:
                        self.keyboard.release(target_key)
                except Exception:
                    pass

    def close(self):
        if self.mode == "evdev" and self.ui:
            try:
                self.ui.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Receiver Local Dialogs (With JSON History Memory)
# ---------------------------------------------------------------------------


class LocalRunScriptDialog(QDialog):
    def __init__(self, config: dict, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("Run Script / Executable on Display")
        self.setFixedWidth(520)
        self.setStyleSheet(
            """
            QDialog {
                background-color: #1a1e29;
                border: 1px solid #3d475f;
                border-radius: 10px;
            }
            QLabel {
                color: #ffffff;
                font-family: 'Segoe UI', sans-serif;
                font-size: 12px;
            }
            QLineEdit, QComboBox {
                background: #262c3b;
                border: 1px solid #3d475f;
                color: #ffffff;
                border-radius: 6px;
                padding: 7px 10px;
                font-size: 12px;
            }
            QPushButton {
                background-color: #0078d4;
                color: white;
                border: none;
                border-radius: 6px;
                font-weight: bold;
                font-size: 12px;
                padding: 7px 16px;
            }
            QPushButton:hover {
                background-color: #106ebe;
            }
            QPushButton#browse_btn, QPushButton#cancel_btn {
                background-color: #262c3b;
                border: 1px solid #3d475f;
            }
            QPushButton#browse_btn:hover, QPushButton#cancel_btn:hover {
                background-color: #333c4d;
            }
            """
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        header_layout = QHBoxLayout()
        header_layout.setSpacing(8)
        icon_lbl = QLabel()
        icon_lbl.setPixmap(svg_to_pixmap(REC_SVG_ROCKET, 20, 20, "#00a2ed"))
        title_lbl = QLabel("Run Application / Script on Display")
        title_lbl.setStyleSheet("font-weight: bold; font-size: 15px; color: #00a2ed;")
        header_layout.addWidget(icon_lbl)
        header_layout.addWidget(title_lbl)
        header_layout.addStretch()
        layout.addLayout(header_layout)

        layout.addWidget(QLabel("Executable / Shortcut / Script Path:"))
        path_layout = QHBoxLayout()
        self.path_combo = QComboBox()
        self.path_combo.setEditable(True)
        self.path_combo.lineEdit().setPlaceholderText("Select or enter .exe, .lnk, .bat, .py...")

        recent_scripts = self.config.get("recent_scripts", [])
        last_path = self.config.get("last_script_path", "")
        if last_path and last_path not in recent_scripts:
            recent_scripts.insert(0, last_path)

        for p in recent_scripts:
            self.path_combo.addItem(p)
        if last_path:
            self.path_combo.setEditText(last_path)

        self.browse_btn = QPushButton("Browse...")
        self.browse_btn.setObjectName("browse_btn")
        self.browse_btn.clicked.connect(self._browse_file)
        path_layout.addWidget(self.path_combo)
        path_layout.addWidget(self.browse_btn)
        layout.addLayout(path_layout)

        layout.addWidget(QLabel("Arguments (sys.argv, optional):"))
        self.args_edit = QLineEdit()
        self.args_edit.setPlaceholderText("e.g. 30 --fullscreen -v (optional)")
        self.args_edit.setText(self.config.get("last_script_args", ""))
        self.args_edit.returnPressed.connect(self.accept)
        layout.addWidget(self.args_edit)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setObjectName("cancel_btn")
        self.cancel_btn.clicked.connect(self.reject)

        self.run_btn = QPushButton("Execute Now")
        self.run_btn.setIcon(svg_to_icon(REC_SVG_ROCKET, 14, "#ffffff"))
        self.run_btn.clicked.connect(self.accept)

        btn_layout.addWidget(self.cancel_btn)
        btn_layout.addWidget(self.run_btn)
        layout.addLayout(btn_layout)

    def _browse_file(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Executable or Script",
            APP_DIR,
            "Executables & Shortcuts (*.exe *.lnk *.bat *.cmd *.py *.sh);;All Files (*.*)",
        )
        if file_path:
            self.path_combo.setEditText(file_path)

    def get_data(self) -> tuple[str, str]:
        return self.path_combo.currentText().strip(), self.args_edit.text().strip()


class LocalTimerDialog(QDialog):
    def __init__(self, config: dict, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("Set Display Timer")
        self.setFixedWidth(380)
        self.setStyleSheet(
            """
            QDialog {
                background-color: #1a1e29;
                border: 1px solid #3d475f;
                border-radius: 10px;
            }
            QLabel {
                color: #ffffff;
                font-family: 'Segoe UI', sans-serif;
                font-size: 12px;
            }
            QLineEdit {
                background: #262c3b;
                border: 1px solid #3d475f;
                color: #ffffff;
                border-radius: 6px;
                padding: 7px 10px;
                font-size: 13px;
            }
            QPushButton {
                background-color: #0078d4;
                color: white;
                border: none;
                border-radius: 6px;
                font-weight: bold;
                font-size: 12px;
                padding: 7px 18px;
            }
            QPushButton:hover {
                background-color: #106ebe;
            }
            QPushButton#cancel_btn {
                background-color: #262c3b;
                border: 1px solid #3d475f;
            }
            """
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        header_layout = QHBoxLayout()
        header_layout.setSpacing(8)
        icon_lbl = QLabel()
        icon_lbl.setPixmap(svg_to_pixmap(REC_SVG_TIMER, 20, 20, "#00d084"))
        title_lbl = QLabel("Start Timer on Receiver Display")
        title_lbl.setStyleSheet("font-weight: bold; font-size: 15px; color: #00d084;")
        header_layout.addWidget(icon_lbl)
        header_layout.addWidget(title_lbl)
        header_layout.addStretch()
        layout.addLayout(header_layout)

        layout.addWidget(QLabel("Enter duration (e.g. 30, 5m, 10:00, or raw number):"))
        self.timer_edit = QLineEdit()
        self.timer_edit.setPlaceholderText("30, 5m, 10:00...")
        raw_prev = self.config.get("last_timer_args", "").strip().strip('"').strip("'")
        self.timer_edit.setText(raw_prev)
        self.timer_edit.returnPressed.connect(self.accept)
        layout.addWidget(self.timer_edit)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setObjectName("cancel_btn")
        self.cancel_btn.clicked.connect(self.reject)

        self.start_btn = QPushButton("Start Timer")
        self.start_btn.setIcon(svg_to_icon(REC_SVG_TIMER, 14, "#ffffff"))
        self.start_btn.clicked.connect(self.accept)

        btn_layout.addWidget(self.cancel_btn)
        btn_layout.addWidget(self.start_btn)
        layout.addLayout(btn_layout)

    def get_args(self) -> str:
        val = self.timer_edit.text().strip().strip('"').strip("'")
        return val


# ---------------------------------------------------------------------------
# Server & Communication Threads
# ---------------------------------------------------------------------------


class DiscoveryBeaconThread(QThread):
    def __init__(self, get_pin_func, get_pin_req_func):
        super().__init__()
        self.setObjectName("BeaconThread")
        self.get_pin_func = get_pin_func
        self.get_pin_req_func = get_pin_req_func
        self.running = True

    def run(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

        while self.running:
            try:
                local_ip = get_local_ip()
                pin_req = bool(self.get_pin_req_func())
                payload = json.dumps(
                    {"service": "MrCoopersScreenShare", "ip": local_ip, "pin_required": pin_req}
                ).encode("utf-8")
                sock.sendto(payload, ("255.255.255.255", DISCOVERY_PORT))
            except Exception as e:
                logger.error(f"Discovery broadcast error: {e}")

            for _ in range(15):
                if not self.running:
                    break
                self.msleep(100)
        sock.close()

    def stop(self):
        self.running = False
        self.wait(1000)


class VideoServerThread(QThread):
    frame_received = Signal(QImage)
    client_connected = Signal(str)
    client_disconnected = Signal()

    def __init__(self, get_pin_func, get_pin_req_func, port: int = VIDEO_PORT):
        super().__init__()
        self.setObjectName("VideoServerThread")
        self.port = port
        self.get_pin_func = get_pin_func
        self.get_pin_req_func = get_pin_req_func
        self.running = True
        self.server_sock: Optional[socket.socket] = None

    def run(self):
        self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, SOCKET_BUFFER_SIZE)
        except Exception:
            pass

        self.server_sock.bind(("0.0.0.0", self.port))
        self.server_sock.listen(1)
        self.server_sock.settimeout(0.5)

        while self.running:
            try:
                conn, addr = self.server_sock.accept()
            except socket.timeout:
                continue
            except Exception:
                break

            if self._verify_handshake(conn, addr[0]):
                self.client_connected.emit(addr[0])
                self._handle_client(conn, addr[0])
            else:
                try:
                    conn.close()
                except Exception:
                    pass

        if self.server_sock:
            try:
                self.server_sock.close()
            except Exception:
                pass

    def _verify_handshake(self, conn: socket.socket, client_ip: str) -> bool:
        try:
            conn.settimeout(5.0)
            header = recv_exact(conn, 4)
            if not header:
                return False
            size = struct.unpack(">L", header)[0]
            raw_payload = recv_exact(conn, size)
            if not raw_payload:
                return False

            data = json.loads(raw_payload.decode("utf-8"))
            pin_req = bool(self.get_pin_req_func())
            client_pin = str(data.get("pin", "")).strip()
            server_pin = str(self.get_pin_func()).strip()

            if pin_req and client_pin != server_pin:
                resp = json.dumps({"auth": False, "msg": "Incorrect PIN"}).encode("utf-8")
                conn.sendall(struct.pack(">L", len(resp)) + resp)
                return False

            resp = json.dumps({"auth": True, "msg": "OK"}).encode("utf-8")
            conn.sendall(struct.pack(">L", len(resp)) + resp)
            conn.settimeout(None)
            return True
        except Exception as e:
            logger.error(f"Handshake exception: {e}")
            return False

    def _handle_client(self, conn: socket.socket, client_ip: str):
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        conn.settimeout(0.5)
        payload_size = struct.calcsize(">L")
        data = bytearray()

        while self.running:
            try:
                while len(data) < payload_size:
                    if not self.running:
                        break
                    try:
                        packet = conn.recv(131072)
                        if not packet:
                            raise ConnectionResetError
                        data.extend(packet)
                    except socket.timeout:
                        continue

                if not self.running:
                    break

                msg_size = struct.unpack(">L", data[:payload_size])[0]
                data = data[payload_size:]

                while len(data) < msg_size:
                    if not self.running:
                        break
                    try:
                        packet = conn.recv(min(msg_size - len(data), 131072))
                        if not packet:
                            raise ConnectionResetError
                        data.extend(packet)
                    except socket.timeout:
                        continue

                if not self.running:
                    break

                frame_data = data[:msg_size]
                data = data[msg_size:]

                np_arr = np.frombuffer(frame_data, np.uint8)
                img = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
                if img is not None:
                    h, w, ch = img.shape
                    qimg = QImage(img.data, w, h, ch * w, QImage.Format_BGR888).copy()
                    self.frame_received.emit(qimg)

            except ConnectionResetError:
                break
            except Exception as ex:
                logger.error(f"Video client processing error from {client_ip}: {ex}")
                break

        try:
            conn.close()
        except Exception:
            pass
        self.client_disconnected.emit()

    def stop(self):
        self.running = False
        if self.server_sock:
            try:
                self.server_sock.close()
            except Exception:
                pass
        self.wait(1000)


class ReverseVideoServerThread(QThread):
    def __init__(self, port: int = REVERSE_VIDEO_PORT, quality: int = 85, fps: int = 30):
        super().__init__()
        self.setObjectName("ReverseVideoServerThread")
        self.port = port
        self.quality = quality
        self.fps = fps
        self.running = True
        self.server_sock: Optional[socket.socket] = None

    def run(self):
        self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, SOCKET_BUFFER_SIZE)
        except Exception:
            pass

        self.server_sock.bind(("0.0.0.0", self.port))
        self.server_sock.listen(1)
        self.server_sock.settimeout(0.5)

        while self.running:
            try:
                conn, addr = self.server_sock.accept()
            except socket.timeout:
                continue
            except Exception:
                break

            logger.debug(f"Reverse video stream client connected from {addr[0]}")
            self._stream_to_viewer(conn)

        if self.server_sock:
            try:
                self.server_sock.close()
            except Exception:
                pass

    def _stream_to_viewer(self, conn: socket.socket):
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        conn.settimeout(None)

        with create_mss_instance() as sct:
            monitor = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
            mon_left = monitor["left"]
            mon_top = monitor["top"]
            encode_params = [int(cv2.IMWRITE_JPEG_QUALITY), self.quality]

            while self.running:
                t_start = time.perf_counter()
                try:
                    raw_frame = sct.grab(monitor)
                    img = np.array(raw_frame)
                    bgr = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
                    render_cursor_on_frame(bgr, mon_left, mon_top)

                    success, enc_img = cv2.imencode(".jpg", bgr, encode_params)
                    if success:
                        data = enc_img.tobytes()
                        conn.sendall(struct.pack(">L", len(data)) + data)
                except Exception:
                    break

                elapsed = time.perf_counter() - t_start
                sleep_sec = (1.0 / max(1, self.fps)) - elapsed
                if sleep_sec > 0:
                    self.msleep(int(sleep_sec * 1000))

        try:
            conn.close()
        except Exception:
            pass

    def stop(self):
        self.running = False
        if self.server_sock:
            try:
                self.server_sock.close()
            except Exception:
                pass
        self.wait(1000)


class AudioServerThread(QThread):
    def __init__(self, port: int = AUDIO_PORT):
        super().__init__()
        self.setObjectName("AudioServerThread")
        self.port = port
        self.running = True
        self.server_sock: Optional[socket.socket] = None

    def run(self):
        if not AUDIO_AVAILABLE:
            return

        self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_sock.bind(("0.0.0.0", self.port))
        self.server_sock.listen(1)
        self.server_sock.settimeout(0.5)

        while self.running:
            try:
                conn, addr = self.server_sock.accept()
                conn.settimeout(3.0)
                hdr = recv_exact(conn, 4)
                sample_rate = DEFAULT_SAMPLE_RATE
                if hdr:
                    sample_rate = struct.unpack(">I", hdr)[0]

                conn.settimeout(0.5)
                try:
                    out_stream = sd.OutputStream(
                        samplerate=sample_rate, channels=CHANNELS, dtype="int16", latency="low"
                    )
                    out_stream.start()
                except Exception as ex:
                    logger.error(f"Failed to start audio playback: {ex}")
                    conn.close()
                    continue

                audio_buf = bytearray()
                frame_bytes = CHANNELS * 2

                while self.running:
                    try:
                        pcm_data = conn.recv(8192)
                        if not pcm_data:
                            break
                        audio_buf.extend(pcm_data)
                        valid_bytes = len(audio_buf) - (len(audio_buf) % frame_bytes)
                        if valid_bytes >= frame_bytes:
                            samples = np.frombuffer(audio_buf[:valid_bytes], dtype=np.int16).reshape(-1, CHANNELS)
                            audio_buf = audio_buf[valid_bytes:]
                            out_stream.write(samples)
                    except socket.timeout:
                        continue
                    except Exception:
                        break

                try:
                    out_stream.stop()
                    out_stream.close()
                except Exception:
                    pass
                conn.close()
            except socket.timeout:
                continue
            except Exception:
                break

        if self.server_sock:
            try:
                self.server_sock.close()
            except Exception:
                pass

    def stop(self):
        self.running = False
        if self.server_sock:
            try:
                self.server_sock.close()
            except Exception:
                pass
        self.wait(1000)


class ControlServerThread(QThread):
    command_received = Signal(dict)

    def __init__(self, port: int = CONTROL_PORT):
        super().__init__()
        self.setObjectName("ControlServerThread")
        self.port = port
        self.running = True
        self.client_conn: Optional[socket.socket] = None
        self.server_sock: Optional[socket.socket] = None
        self._send_lock = threading.Lock()

    def run(self):
        self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_sock.bind(("0.0.0.0", self.port))
        self.server_sock.listen(1)
        self.server_sock.settimeout(0.5)

        while self.running:
            try:
                conn, addr = self.server_sock.accept()
                conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                conn.settimeout(0.5)
                with self._send_lock:
                    self.client_conn = conn
                logger.info(f"Control channel client connected from {addr[0]}")
            except socket.timeout:
                continue
            except Exception:
                break

            payload_size = struct.calcsize(">L")
            data = bytearray()

            while self.running and self.client_conn:
                try:
                    while len(data) < payload_size:
                        if not self.running:
                            break
                        try:
                            packet = conn.recv(2048)
                            if not packet:
                                raise ConnectionResetError
                            data.extend(packet)
                        except socket.timeout:
                            continue

                    if not self.running:
                        break

                    packed_size = data[:payload_size]
                    data = data[payload_size:]
                    msg_size = struct.unpack(">L", packed_size)[0]

                    while len(data) < msg_size:
                        if not self.running:
                            break
                        try:
                            packet = conn.recv(min(msg_size - len(data), 4096))
                            if not packet:
                                raise ConnectionResetError
                            data.extend(packet)
                        except socket.timeout:
                            continue

                    if not self.running:
                        break

                    raw_msg = data[:msg_size]
                    data = data[msg_size:]
                    msg_obj = json.loads(raw_msg.decode("utf-8"))
                    self.command_received.emit(msg_obj)

                except (socket.timeout, BlockingIOError):
                    continue
                except ConnectionResetError:
                    break
                except Exception:
                    break

            with self._send_lock:
                try:
                    conn.close()
                except Exception:
                    pass
                self.client_conn = None

        if self.server_sock:
            try:
                self.server_sock.close()
            except Exception:
                pass

    def send_event(self, event_data: dict):
        with self._send_lock:
            if self.client_conn:
                try:
                    msg = json.dumps(event_data).encode("utf-8")
                    self.client_conn.sendall(struct.pack(">L", len(msg)) + msg)
                    logger.debug(f"[Touch Send] {event_data.get('type')} at {event_data.get('x')}, {event_data.get('y')}")
                except Exception as ex:
                    logger.error(f"Failed to transmit touch event: {ex}")
                    self.client_conn = None
            else:
                logger.warning("send_event called but no active client connection exists!")

    def stop(self):
        self.running = False
        with self._send_lock:
            if self.client_conn:
                try:
                    self.client_conn.close()
                except Exception:
                    pass
                self.client_conn = None
        if self.server_sock:
            try:
                self.server_sock.close()
            except Exception:
                pass
        self.wait(1000)


# ---------------------------------------------------------------------------
# Canvas & Standby Widgets (Solid Edge Cleared & Zero White Margin Flicker)
# ---------------------------------------------------------------------------


class StandbyContainer(QWidget):
    """Standby Screen Container ensuring all right-click events trigger the context menu."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAutoFillBackground(False)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#0d111a"))

    def mousePressEvent(self, event):
        if event.button() == Qt.RightButton:
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.RightButton:
            main_win = self.window()
            if hasattr(main_win, "show_context_menu"):
                main_win.show_context_menu(QCursor.pos())
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event):
        main_win = self.window()
        if hasattr(main_win, "show_context_menu"):
            main_win.show_context_menu(QCursor.pos())
            event.accept()
        else:
            super().contextMenuEvent(event)


class TouchDisplayCanvas(QWidget):
    """
    High-DPI Canvas with full hardware support for Windows 11 multi-touch events
    (QTouchEvent) and traditional mouse/trackpad events.
    Guarantees solid edge bounds to eliminate white flickering at any resolution.
    """

    def __init__(self, control_server: Optional[ControlServerThread] = None, parent=None):
        super().__init__(parent)
        self.control_server = control_server
        self.current_frame: Optional[QPixmap] = None
        self.setAttribute(Qt.WA_AcceptTouchEvents, True)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAutoFillBackground(False)
        self.setMouseTracking(True)
        self.setStyleSheet("background-color: #000000;")

    def update_frame(self, qimage: QImage):
        self.current_frame = QPixmap.fromImage(qimage)
        self.update()

    def _get_video_rect(self) -> QRect:
        if not self.current_frame or self.current_frame.isNull():
            return self.rect()
        pix_size = self.current_frame.size()
        pix_size.scale(self.size(), Qt.KeepAspectRatio)
        return QRect(
            (self.width() - pix_size.width()) // 2,
            (self.height() - pix_size.height()) // 2,
            pix_size.width(),
            pix_size.height(),
        )

    def _normalize_pos(self, pos: QPointF) -> Optional[tuple[float, float]]:
        r = self._get_video_rect()
        if r.width() == 0 or r.height() == 0:
            return None
        nx = (pos.x() - r.x()) / r.width()
        ny = (pos.y() - r.y()) / r.height()
        return (nx, ny) if 0.0 <= nx <= 1.0 and 0.0 <= ny <= 1.0 else None

    def event(self, event: QEvent) -> bool:
        if event.type() in (
            QEvent.TouchBegin,
            QEvent.TouchUpdate,
            QEvent.TouchEnd,
            QEvent.TouchCancel,
        ):
            self._handle_touch_event(event)
            return True
        return super().event(event)

    def _handle_touch_event(self, event):
        if not self.control_server:
            return
        points = event.points()
        if not points:
            return
        pt = points[0]
        norm = self._normalize_pos(pt.position())
        if not norm:
            return

        ev_type = event.type()
        if ev_type == QEvent.TouchBegin:
            self.control_server.send_event(
                {"type": "touch_down", "x": norm[0], "y": norm[1], "button": "left"}
            )
        elif ev_type == QEvent.TouchUpdate:
            self.control_server.send_event(
                {"type": "touch_move", "x": norm[0], "y": norm[1]}
            )
        elif ev_type in (QEvent.TouchEnd, QEvent.TouchCancel):
            self.control_server.send_event(
                {"type": "touch_up", "x": norm[0], "y": norm[1], "button": "left"}
            )

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.black)

        if self.current_frame and not self.current_frame.isNull():
            painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
            target_rect = self._get_video_rect()
            painter.drawPixmap(target_rect, self.current_frame)

    def mousePressEvent(self, event):
        if event.button() == Qt.RightButton:
            event.accept()
            return
        if not self.control_server:
            return
        norm = self._normalize_pos(event.position())
        if norm:
            self.control_server.send_event(
                {"type": "mouse_down", "x": norm[0], "y": norm[1], "button": "left"}
            )

    def mouseMoveEvent(self, event):
        if not self.control_server:
            return
        norm = self._normalize_pos(event.position())
        if norm:
            self.control_server.send_event(
                {"type": "mouse_move", "x": norm[0], "y": norm[1]}
            )

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.RightButton:
            main_win = self.window()
            if hasattr(main_win, "show_context_menu"):
                main_win.show_context_menu(QCursor.pos())
            event.accept()
            return
        if not self.control_server:
            return
        norm = self._normalize_pos(event.position())
        if norm:
            self.control_server.send_event(
                {"type": "mouse_up", "x": norm[0], "y": norm[1], "button": "left"}
            )

    def wheelEvent(self, event):
        if self.control_server:
            self.control_server.send_event(
                {"type": "scroll", "dy": event.angleDelta().y()}
            )

    def contextMenuEvent(self, event):
        main_win = self.window()
        if hasattr(main_win, "show_context_menu"):
            main_win.show_context_menu(QCursor.pos())
            event.accept()
        else:
            event.ignore()


class ReceiverMainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MrCoopersScreenShare - Receiver")
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)

        # Force black palette and opaque settings to eliminate DWM flash
        palette = self.palette()
        palette.setColor(QPalette.Window, Qt.black)
        palette.setColor(QPalette.Base, Qt.black)
        self.setPalette(palette)
        self.setAutoFillBackground(False)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setStyleSheet("QMainWindow { background-color: #000000; }")

        app_icon = create_application_icon()
        self.setWindowIcon(app_icon)

        self.config = load_receiver_config()
        self.hide_details = self.config.get("hide_details", False)
        self.pin = f"{random.randint(1000, 9999)}"
        self._pin_required = False
        self.pin_req_cb: Optional[QCheckBox] = None

        with create_mss_instance() as sct:
            mon = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
            scr_w, scr_h = mon["width"], mon["height"]
            mon_l, mon_t = mon["left"], mon["top"]
        self.input_injector = UniversalInputInjector(mon_l, mon_t, scr_w, scr_h)

        # Initialize network servers with safe callbacks
        self.control_thread = ControlServerThread()
        self.audio_thread = AudioServerThread()
        self.video_thread = VideoServerThread(self.get_pin, self.is_pin_required)
        self.beacon_thread = DiscoveryBeaconThread(self.get_pin, self.is_pin_required)
        self.reverse_video_thread = ReverseVideoServerThread()

        self._setup_ui()
        self._apply_details_visibility()

        self.video_thread.frame_received.connect(self.on_frame)
        self.video_thread.client_connected.connect(self.on_connected)
        self.video_thread.client_disconnected.connect(self.on_disconnected)
        self.control_thread.command_received.connect(self.on_control_command)

        for th in (
            self.control_thread,
            self.audio_thread,
            self.video_thread,
            self.beacon_thread,
            self.reverse_video_thread,
        ):
            th.start()

        self._ensure_frameless_style()

    def _ensure_frameless_style(self):
        if sys.platform == "win32":
            try:
                hwnd = int(self.winId())

                # Set class background to pure black to eliminate white resize/erase flash
                GCLP_HBRBACKGROUND = -10
                black_brush = ctypes.windll.gdi32.CreateSolidBrush(0x00000000)
                ctypes.windll.user32.SetClassLongPtrW(hwnd, GCLP_HBRBACKGROUND, black_brush)

                # Strip Windows title bars & standard borders
                GWL_STYLE = -16
                WS_CAPTION = 0x00C00000
                WS_THICKFRAME = 0x00040000
                style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_STYLE)
                new_style = style & ~WS_CAPTION & ~WS_THICKFRAME
                if new_style != style:
                    ctypes.windll.user32.SetWindowLongW(hwnd, GWL_STYLE, new_style)

                # Disable Windows 11 rounded corners and border outline
                DWMWA_WINDOW_CORNER_PREFERENCE = 33
                DWMWCP_DONOTROUND = c_int(1)
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd,
                    DWMWA_WINDOW_CORNER_PREFERENCE,
                    byref(DWMWCP_DONOTROUND),
                    sizeof(DWMWCP_DONOTROUND),
                )

                DWMWA_BORDER_COLOR = 34
                color_none = c_int(0xFFFFFFFE)
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd,
                    DWMWA_BORDER_COLOR,
                    byref(color_none),
                    sizeof(color_none),
                )

                # Commit window frame style changes immediately
                ctypes.windll.user32.SetWindowPos(
                    hwnd, 0, 0, 0, 0, 0, 0x0020 | 0x0002 | 0x0001 | 0x0004
                )
            except Exception as e:
                logger.debug(f"Frameless enforcement notice: {e}")

    def set_receiver_fullscreen(self, fullscreen: bool):
        if fullscreen:
            self.showFullScreen()
        else:
            self.showNormal()
            screen = self.screen() or QApplication.primaryScreen()
            if screen:
                geom = screen.geometry()
                target_w = min(1280, int(geom.width() * 0.8))
                target_h = min(720, int(geom.height() * 0.8))
                self.setGeometry(
                    geom.x() + (geom.width() - target_w) // 2,
                    geom.y() + (geom.height() - target_h) // 2,
                    target_w,
                    target_h,
                )
        self._ensure_frameless_style()

    def get_pin(self) -> str:
        return self.pin

    def is_pin_required(self) -> bool:
        if self.pin_req_cb is not None:
            return self.pin_req_cb.isChecked()
        return self._pin_required

    def _on_pin_req_toggled(self, checked: bool):
        self._pin_required = checked

    def _setup_ui(self):
        self.stack = QStackedWidget()
        self.stack.setContentsMargins(0, 0, 0, 0)
        self.setCentralWidget(self.stack)

        self.standby = StandbyContainer()
        sb_layout = QVBoxLayout(self.standby)
        sb_layout.setAlignment(Qt.AlignCenter)
        sb_layout.setSpacing(18)

        self.details_container = QWidget()
        self.details_layout = QVBoxLayout(self.details_container)
        self.details_layout.setAlignment(Qt.AlignCenter)
        self.details_layout.setSpacing(18)

        self.title_lbl = QLabel("MrCoopersScreenShare")
        self.title_lbl.setFont(QFont("Segoe UI", 36, QFont.Bold))
        self.title_lbl.setStyleSheet("color: #00a2ed;")

        self.ip_lbl = QLabel(f"Display IP: {get_local_ip()}")
        self.ip_lbl.setFont(QFont("Segoe UI", 24))
        self.ip_lbl.setStyleSheet("color: #ffffff;")

        self.pin_lbl = QLabel(f"PIN: {self.pin}")
        self.pin_lbl.setFont(QFont("Segoe UI", 30, QFont.Bold))
        self.pin_lbl.setStyleSheet("color: #00d084; letter-spacing: 4px;")

        self.pin_req_cb = QCheckBox("Require 4-digit PIN to Connect")
        self.pin_req_cb.setChecked(False)
        self.pin_req_cb.setStyleSheet("color: #8f9bb3; font-size: 15px; margin-top: 10px;")
        self.pin_req_cb.toggled.connect(self._on_pin_req_toggled)

        self.hint_lbl = QLabel("Right-click anywhere for menu  |  Press ESC / F11 to toggle fullscreen")
        self.hint_lbl.setStyleSheet("color: #4b5568; font-size: 13px; margin-top: 20px;")

        self.details_layout.addWidget(self.title_lbl, alignment=Qt.AlignCenter)
        self.details_layout.addWidget(self.ip_lbl, alignment=Qt.AlignCenter)
        self.details_layout.addWidget(self.pin_lbl, alignment=Qt.AlignCenter)
        self.details_layout.addWidget(self.pin_req_cb, alignment=Qt.AlignCenter)
        self.details_layout.addWidget(self.hint_lbl, alignment=Qt.AlignCenter)

        sb_layout.addWidget(self.details_container, alignment=Qt.AlignCenter)

        for w in (
            self.details_container,
            self.title_lbl,
            self.ip_lbl,
            self.pin_lbl,
            self.hint_lbl,
        ):
            w.installEventFilter(self)

        self.canvas = TouchDisplayCanvas(control_server=self.control_thread)
        self.stack.addWidget(self.standby)
        self.stack.addWidget(self.canvas)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.ContextMenu:
            self.show_context_menu(QCursor.pos())
            return True
        elif event.type() == QEvent.MouseButtonRelease and event.button() == Qt.RightButton:
            self.show_context_menu(QCursor.pos())
            return True
        return super().eventFilter(watched, event)

    def _apply_details_visibility(self):
        self.details_container.setVisible(not self.hide_details)

    def toggle_details_visibility(self):
        self.hide_details = not self.hide_details
        self._apply_details_visibility()
        self.config["hide_details"] = self.hide_details
        save_receiver_config(self.config)

    def execute_local_script(self, target_path: str, args_str: str = ""):
        target_path = target_path.strip()
        args_str = args_str.strip()
        if not target_path:
            return

        self.config["last_script_path"] = target_path
        self.config["last_script_args"] = args_str
        recent = self.config.get("recent_scripts", [])
        if target_path in recent:
            recent.remove(target_path)
        recent.insert(0, target_path)
        self.config["recent_scripts"] = recent[:15]
        save_receiver_config(self.config)

        try:
            parsed_args = shlex.split(args_str) if args_str else []
            parsed_args = [arg.strip().strip('"').strip("'") for arg in parsed_args if arg.strip()]

            if target_path.lower().endswith(".py"):
                cmd = [sys.executable, target_path] + parsed_args
            else:
                cmd = [target_path] + parsed_args

            creationflags = 0
            if sys.platform == "win32":
                creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP

            subprocess.Popen(
                cmd,
                creationflags=creationflags,
                start_new_session=True if sys.platform != "win32" else False,
            )
            logger.info(f"Spawned local process: {cmd}")
        except Exception as ex:
            logger.error(f"Failed to execute local process '{target_path}': {ex}")

    def execute_local_timer(self, args_str: str = ""):
        clean_arg = args_str.strip().strip('"').strip("'").strip()
        self.config["last_timer_args"] = clean_arg
        save_receiver_config(self.config)

        saved_path = self.config.get("last_script_path", "")
        timer_candidates = []
        if saved_path and ("timer" in os.path.basename(saved_path).lower()):
            timer_candidates.append(saved_path)

        timer_candidates.extend([
            os.path.join(APP_DIR, "MrCoopersTimer.exe"),
            os.path.join(APP_DIR, "timer.exe"),
            os.path.join(APP_DIR, "timer.py"),
            os.path.join(APP_DIR, "main.py"),
            os.path.join(APP_DIR, "timer"),
            "MrCoopersTimer.exe",
            "timer.exe",
            "timer",
        ])

        target_bin = next((c for c in timer_candidates if os.path.exists(c)), "MrCoopersTimer.exe")

        try:
            cmd = []
            if target_bin.lower().endswith(".py"):
                cmd = [sys.executable, target_bin]
            else:
                cmd = [target_bin]

            if clean_arg:
                for tok in clean_arg.split():
                    tok_clean = tok.strip().strip('"').strip("'")
                    if tok_clean:
                        cmd.append(tok_clean)

            creationflags = 0
            if sys.platform == "win32":
                creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP

            subprocess.Popen(
                cmd,
                creationflags=creationflags,
                start_new_session=True if sys.platform != "win32" else False,
            )
            logger.info(f"Launched timer with raw sys.argv: {cmd}")
        except Exception as ex:
            logger.error(f"Failed to run timer: {ex}")

    def open_local_script_dialog(self):
        dlg = LocalRunScriptDialog(self.config, self)
        if dlg.exec() == QDialog.Accepted:
            path, args = dlg.get_data()
            if path:
                self.execute_local_script(path, args)

    def open_local_timer_dialog(self):
        dlg = LocalTimerDialog(self.config, self)
        if dlg.exec() == QDialog.Accepted:
            args = dlg.get_args()
            self.execute_local_timer(args)

    def on_control_command(self, cmd: dict):
        cmd_type = cmd.get("type")

        if cmd_type == "remote_input":
            event_data = cmd.get("event", {})
            self.input_injector.execute(event_data)
            return

        if cmd_type == "open_file_picker":
            self.open_local_script_dialog()
            return

        if cmd_type == "window_control":
            action = cmd.get("action")
            if action == "maximize":
                if self.isMinimized():
                    self.showNormal()
                self.set_receiver_fullscreen(True)
                self.raise_()
                self.activateWindow()
            elif action == "normal":
                if self.isMinimized():
                    self.showNormal()
                self.set_receiver_fullscreen(False)
                self.raise_()
                self.activateWindow()
            elif action == "minimize":
                self.showMinimized()

        elif cmd_type == "run_script":
            target_path = cmd.get("path", "").strip()
            args_str = cmd.get("args", "").strip()
            self.execute_local_script(target_path, args_str)

        elif cmd_type == "timer":
            args_str = cmd.get("args", "").strip()
            self.execute_local_timer(args_str)

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key_Escape:
            self.close()
        elif event.key() == Qt.Key_F11:
            self.set_receiver_fullscreen(not self.isFullScreen())
        super().keyPressEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.RightButton:
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.RightButton:
            self.show_context_menu(QCursor.pos())
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event):
        self.show_context_menu(QCursor.pos())
        event.accept()

    def show_context_menu(self, global_pos: Optional[QPoint] = None):
        if global_pos is None:
            global_pos = QCursor.pos()

        menu = QMenu(self)
        menu.setStyleSheet(
            """
            QMenu {
                background-color: #1a1e29;
                color: #ffffff;
                border: 1px solid #3d475f;
                border-radius: 8px;
                padding: 4px;
                font-family: 'Segoe UI', sans-serif;
                font-size: 13px;
            }
            QMenu::item {
                padding: 7px 24px 7px 12px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background-color: #0078d4;
                color: #ffffff;
            }
            QMenu::separator {
                height: 1px;
                background: #333c4d;
                margin: 4px 6px;
            }
        """
        )

        run_act = QAction("Run Script / Executable on Display...", self)
        run_act.setIcon(svg_to_icon(REC_SVG_ROCKET, 16, "#00a2ed"))
        run_act.triggered.connect(self.open_local_script_dialog)
        menu.addAction(run_act)

        timer_act = QAction("Show Timer...", self)
        timer_act.setIcon(svg_to_icon(REC_SVG_TIMER, 16, "#00d084"))
        timer_act.triggered.connect(self.open_local_timer_dialog)
        menu.addAction(timer_act)

        menu.addSeparator()

        info_toggle_text = "Show Standby Details (IP/PIN)" if self.hide_details else "Hide Standby Details (IP/PIN)"
        toggle_info_act = QAction(info_toggle_text, self)
        toggle_info_act.setIcon(svg_to_icon(REC_SVG_INFO, 16, "#ffffff"))
        toggle_info_act.triggered.connect(self.toggle_details_visibility)
        menu.addAction(toggle_info_act)

        menu.addSeparator()

        if self.isFullScreen():
            fs_act = QAction("Exit Fullscreen (F11)", self)
            fs_act.setIcon(svg_to_icon(REC_SVG_EXIT_FULLSCREEN, 16, "#ffffff"))
            fs_act.triggered.connect(lambda: self.set_receiver_fullscreen(False))
        else:
            fs_act = QAction("Enter Fullscreen (F11)", self)
            fs_act.setIcon(svg_to_icon(REC_SVG_FULLSCREEN, 16, "#ffffff"))
            fs_act.triggered.connect(lambda: self.set_receiver_fullscreen(True))
        menu.addAction(fs_act)

        if self.stack.currentWidget() == self.canvas:
            disc_act = QAction("Disconnect Stream", self)
            disc_act.setIcon(svg_to_icon(REC_SVG_DISCONNECT, 16, "#f37021"))
            disc_act.triggered.connect(self.on_disconnected)
            menu.addAction(disc_act)

        menu.addSeparator()

        exit_act = QAction("Exit Application (Esc)", self)
        exit_act.setIcon(svg_to_icon(REC_SVG_LOGOUT, 16, "#ff6b6b"))
        exit_act.triggered.connect(self.close)
        menu.addAction(exit_act)

        menu.exec(global_pos)

    def on_connected(self, ip: str):
        self.canvas.control_server = self.control_thread
        self.stack.setCurrentWidget(self.canvas)
        self._ensure_frameless_style()

    def on_disconnected(self):
        self.canvas.current_frame = None
        self.stack.setCurrentWidget(self.standby)
        self._ensure_frameless_style()

    def on_frame(self, img: QImage):
        self.canvas.update_frame(img)

    def closeEvent(self, event):
        self.beacon_thread.stop()
        self.video_thread.stop()
        self.reverse_video_thread.stop()
        self.audio_thread.stop()
        self.control_thread.stop()
        self.input_injector.close()
        event.accept()


if __name__ == "__main__":
    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "mrcoopers.screenshare.receiver.1"
            )
        except Exception:
            pass

    app = QApplication(sys.argv)

    # Force application-wide black background for any transient unpainted frames
    app_palette = app.palette()
    app_palette.setColor(QPalette.Window, Qt.black)
    app_palette.setColor(QPalette.Base, Qt.black)
    app.setPalette(app_palette)

    app_icon = create_application_icon()
    app.setWindowIcon(app_icon)

    win = ReceiverMainWindow()
    win.setWindowIcon(app_icon)
    win.set_receiver_fullscreen(True)
    sys.exit(app.exec())