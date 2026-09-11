# receiver.py
"""
MrCoopersScreenShare - Receiver (Interactive Touch Display & Sound Hub)
Features: Fullscreen Frameless Mode, Local Script / Executable Launcher with JSON History Memory,
          Local & Remote Timer Launcher with Robust sys.argv Argument Passing (Raw Numbers / Unit Strings),
          Reverse Desktop Screen Streaming & Interactive Remote Input (Mouse + Keyboard / Hotkeys),
          Remote Window Management (Maximize, Normal, Minimize),
          Right-Click Context Menu (Run Script, Show Timer, Fullscreen, Standby Details Visibility),
          Dynamic Audio Playback, Native Win32 / Universal Input Injection (pynput/evdev),
          UDP Discovery Beacon, 4-Digit PIN Authentication.
"""

import ctypes
from ctypes import Structure, byref, c_long
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

# Enable Per-Monitor High DPI Awareness on Windows early
if sys.platform == "win32":
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

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
from PySide6.QtCore import QEvent, QPointF, QRect, Qt, QThread, Signal
from PySide6.QtGui import (
    QAction,
    QCursor,
    QFont,
    QImage,
    QKeyEvent,
    QPainter,
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

APP_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE_PATH = os.path.join(APP_DIR, "mrcoopers_receiver.log")
CONFIG_FILE_PATH = os.path.join(APP_DIR, "receiver_config.json")

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

# Universal Input Injection (pynput fallback)
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
# Universal Input Injector (Direct Win32 API + pynput / evdev fallback)
# ---------------------------------------------------------------------------


class UniversalInputInjector:
    # Win32 Mouse & Keyboard Event Flags
    MOUSEEVENTF_LEFTDOWN = 0x0002
    MOUSEEVENTF_LEFTUP = 0x0004
    MOUSEEVENTF_RIGHTDOWN = 0x0008
    MOUSEEVENTF_RIGHTUP = 0x0010
    MOUSEEVENTF_MIDDLEDOWN = 0x0020
    MOUSEEVENTF_MIDDLEUP = 0x0040
    MOUSEEVENTF_WHEEL = 0x0800
    KEYEVENTF_EXTENDEDKEY = 0x0001
    KEYEVENTF_KEYUP = 0x0002

    # Virtual Key Mappings for Win32
    QT_KEY_TO_VK = {
        0x01000000: 0x1B,  # Escape
        0x01000001: 0x09,  # Tab
        0x01000002: 0x09,  # Backtab
        0x01000003: 0x08,  # Backspace
        0x01000004: 0x0D,  # Return
        0x01000005: 0x0D,  # Enter
        0x01000006: 0x2D,  # Insert
        0x01000007: 0x2E,  # Delete
        0x01000008: 0x13,  # Pause
        0x01000009: 0x2A,  # Print
        0x01000010: 0x24,  # Home
        0x01000011: 0x23,  # End
        0x01000012: 0x25,  # Left
        0x01000013: 0x26,  # Up
        0x01000014: 0x27,  # Right
        0x01000015: 0x28,  # Down
        0x01000016: 0x21,  # PageUp
        0x01000017: 0x22,  # PageDown
        0x01000020: 0x10,  # Shift
        0x01000021: 0x11,  # Control
        0x01000022: 0x5B,  # Meta / Windows key
        0x01000023: 0x12,  # Alt
        0x01000024: 0x14,  # CapsLock
        0x01000025: 0x90,  # NumLock
        0x01000026: 0x91,  # ScrollLock
        0x20: 0x20,        # Space
    }

    # Add F1-F24 function keys
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
        self.is_win32 = sys.platform == "win32"
        self.mouse = None
        self.keyboard = None

        if not self.is_win32 and PYNPUT_AVAILABLE:
            try:
                self.mouse = MouseController()
                self.keyboard = KeyboardController()
            except Exception as ex:
                logger.warning(f"Failed to initialize pynput controller: {ex}")

        print(
            f"[DEBUG Receiver Injector] Initialized injector. Win32={self.is_win32}, "
            f"Bounds=({self.mon_left},{self.mon_top},{self.screen_w}x{self.screen_h})"
        )

    def execute(self, event: dict):
        ev_type = event.get("type")
        nx = event.get("x")
        ny = event.get("y")

        if nx is not None and ny is not None:
            px = self.mon_left + int(np.clip(nx, 0.0, 1.0) * (self.screen_w - 1))
            py = self.mon_top + int(np.clip(ny, 0.0, 1.0) * (self.screen_h - 1))
        else:
            px, py = None, None

        if self.is_win32:
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
                    print(f"[DEBUG Receiver Injector] Win32 Mouse Down: {btn} at ({px}, {py})")

                elif ev_type in ("touch_up", "mouse_up"):
                    btn = event.get("button", "left")
                    if btn == "right":
                        ctypes.windll.user32.mouse_event(self.MOUSEEVENTF_RIGHTUP, 0, 0, 0, 0)
                    elif btn == "middle":
                        ctypes.windll.user32.mouse_event(self.MOUSEEVENTF_MIDDLEUP, 0, 0, 0, 0)
                    else:
                        ctypes.windll.user32.mouse_event(self.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
                    print(f"[DEBUG Receiver Injector] Win32 Mouse Up: {btn} at ({px}, {py})")

                elif ev_type == "scroll":
                    dy = event.get("dy", 0)
                    delta = 120 if dy > 0 else -120
                    ctypes.windll.user32.mouse_event(self.MOUSEEVENTF_WHEEL, 0, 0, delta, 0)
                    print(f"[DEBUG Receiver Injector] Win32 Scroll: dy={dy}")

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
                        print(f"[DEBUG Receiver Injector] Win32 Key {ev_type}: vk=0x{vk:02X}, name='{key_name}', text='{text}'")
                    else:
                        print(f"[DEBUG Receiver Injector] Win32 Unknown Key: code={key_code}, name='{key_name}', text='{text}'")
                return
            except Exception as ex:
                logger.error(f"Win32 input injection exception: {ex}")

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

        title_lbl = QLabel("🚀 Run Application / Script on Display")
        title_lbl.setStyleSheet("font-weight: bold; font-size: 15px; color: #00a2ed;")
        layout.addWidget(title_lbl)

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

        title_lbl = QLabel("⏱️ Start Timer on Receiver Display")
        title_lbl.setStyleSheet("font-weight: bold; font-size: 15px; color: #00d084;")
        layout.addWidget(title_lbl)

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
                pin_req = self.get_pin_req_func()
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
            pin_req = self.get_pin_req_func()
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
            except Exception:
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

            print(f"[DEBUG Receiver Reverse Video] Stream client connected from {addr[0]}")
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
            monitor = sct.monitors[1]
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
        self.server_sock.listen(5)
        self.server_sock.settimeout(0.5)

        print(f"[DEBUG Receiver Control] Listening for control channel on port {self.port}...")

        while self.running:
            try:
                conn, addr = self.server_sock.accept()
                conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                conn.settimeout(0.5)
                with self._send_lock:
                    self.client_conn = conn
                print(f"[DEBUG Receiver Control] Control client connected from {addr[0]}")
            except socket.timeout:
                continue
            except Exception:
                break

            payload_size = struct.calcsize(">L")
            data = bytearray()

            while self.running and self.client_conn:
                try:
                    try:
                        packet = conn.recv(4096)
                        if not packet:
                            print("[DEBUG Receiver Control] Client closed control connection.")
                            break
                        data.extend(packet)
                    except socket.timeout:
                        pass
                    except (BlockingIOError, InterruptedError):
                        continue

                    while len(data) >= payload_size:
                        msg_size = struct.unpack(">L", data[:payload_size])[0]
                        if len(data) < payload_size + msg_size:
                            break

                        raw_msg = data[payload_size : payload_size + msg_size]
                        data = data[payload_size + msg_size :]

                        try:
                            msg_obj = json.loads(raw_msg.decode("utf-8"))
                            self.command_received.emit(msg_obj)
                        except Exception as decode_err:
                            print(f"[DEBUG Receiver Control] JSON decode error: {decode_err}")

                except ConnectionResetError:
                    print("[DEBUG Receiver Control] Client connection reset.")
                    break
                except Exception as ex:
                    print(f"[DEBUG Receiver Control] Exception in control loop: {ex}")
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
                except Exception:
                    self.client_conn = None

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
# Canvas & Main Window
# ---------------------------------------------------------------------------


class TouchDisplayCanvas(QWidget):
    def __init__(self, control_server: ControlServerThread, parent=None):
        super().__init__(parent)
        self.control_server = control_server
        self.current_frame: Optional[QPixmap] = None
        self.setAttribute(Qt.WA_AcceptTouchEvents, True)
        self.setMouseTracking(True)
        self.setStyleSheet("background-color: #0b0e14;")

    def update_frame(self, qimage: QImage):
        self.current_frame = QPixmap.fromImage(qimage)
        self.update()

    def _get_video_rect(self) -> QRect:
        if not self.current_frame:
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
        nx, ny = (pos.x() - r.x()) / r.width(), (pos.y() - r.y()) / r.height()
        return (nx, ny) if 0.0 <= nx <= 1.0 and 0.0 <= ny <= 1.0 else None

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        if self.current_frame:
            painter.drawPixmap(self._get_video_rect(), self.current_frame)

    def mousePressEvent(self, event):
        if event.button() == Qt.RightButton:
            event.ignore()
            return
        norm = self._normalize_pos(event.position())
        if norm:
            self.control_server.send_event(
                {"type": "mouse_down", "x": norm[0], "y": norm[1], "button": "left"}
            )

    def mouseMoveEvent(self, event):
        norm = self._normalize_pos(event.position())
        if norm:
            self.control_server.send_event(
                {"type": "mouse_move", "x": norm[0], "y": norm[1]}
            )

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.RightButton:
            event.ignore()
            return
        norm = self._normalize_pos(event.position())
        if norm:
            self.control_server.send_event(
                {"type": "mouse_up", "x": norm[0], "y": norm[1], "button": "left"}
            )

    def wheelEvent(self, event):
        self.control_server.send_event(
            {"type": "scroll", "dy": event.angleDelta().y()}
        )

    def contextMenuEvent(self, event):
        main_win = self.window()
        if hasattr(main_win, "show_context_menu"):
            main_win.show_context_menu(event.globalPos())
        else:
            event.ignore()


class ReceiverMainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MrCoopersScreenShare - Receiver")
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
        self.setStyleSheet("background-color: #0b0e14;")

        self.config = load_receiver_config()
        self.hide_details = self.config.get("hide_details", False)
        self.pin = f"{random.randint(1000, 9999)}"

        with create_mss_instance() as sct:
            mon = sct.monitors[1]
            scr_w, scr_h = mon["width"], mon["height"]
            mon_l, mon_t = mon["left"], mon["top"]
        self.input_injector = UniversalInputInjector(mon_l, mon_t, scr_w, scr_h)

        self.control_thread = ControlServerThread()
        self.audio_thread = AudioServerThread()
        self.video_thread = VideoServerThread(self.get_pin, self.is_pin_required)
        self.beacon_thread = DiscoveryBeaconThread(self.get_pin, self.is_pin_required)
        self.reverse_video_thread = ReverseVideoServerThread()

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

        self._setup_ui()
        self._apply_details_visibility()

    def get_pin(self) -> str:
        return self.pin

    def is_pin_required(self) -> bool:
        return self.pin_req_cb.isChecked()

    def _setup_ui(self):
        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)

        self.standby = QWidget()
        self.standby.setStyleSheet("background-color: #0d111a;")
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

        self.hint_lbl = QLabel("Right-click anywhere for menu • Press ESC / F11 to toggle fullscreen")
        self.hint_lbl.setStyleSheet("color: #4b5568; font-size: 13px; margin-top: 20px;")

        self.details_layout.addWidget(self.title_lbl, alignment=Qt.AlignCenter)
        self.details_layout.addWidget(self.ip_lbl, alignment=Qt.AlignCenter)
        self.details_layout.addWidget(self.pin_lbl, alignment=Qt.AlignCenter)
        self.details_layout.addWidget(self.pin_req_cb, alignment=Qt.AlignCenter)
        self.details_layout.addWidget(self.hint_lbl, alignment=Qt.AlignCenter)

        sb_layout.addWidget(self.details_container, alignment=Qt.AlignCenter)

        self.canvas = TouchDisplayCanvas(self.control_thread)
        self.stack.addWidget(self.standby)
        self.stack.addWidget(self.canvas)

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
            screen = self.screen() or QApplication.primaryScreen()
            geom = screen.geometry() if screen else QRect(0, 0, 1920, 1080)

            if action == "maximize":
                if self.isMinimized():
                    self.showNormal()
                self.setWindowState(Qt.WindowFullScreen)
                self.setGeometry(geom)
                self.showFullScreen()
                self.raise_()
                self.activateWindow()
            elif action == "normal":
                if self.isMinimized():
                    self.showNormal()
                self.setWindowState(Qt.WindowNoState)
                self.showNormal()
                target_w = min(1280, int(geom.width() * 0.8))
                target_h = min(720, int(geom.height() * 0.8))
                self.setGeometry(
                    geom.x() + (geom.width() - target_w) // 2,
                    geom.y() + (geom.height() - target_h) // 2,
                    target_w,
                    target_h,
                )
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
            if self.isFullScreen():
                self.showNormal()
            else:
                self.showFullScreen()
        super().keyPressEvent(event)

    def contextMenuEvent(self, event):
        self.show_context_menu(event.globalPos())

    def show_context_menu(self, global_pos):
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

        run_act = QAction("🚀 Run Script / Executable on Display...", self)
        run_act.triggered.connect(self.open_local_script_dialog)
        menu.addAction(run_act)

        timer_act = QAction("⏱️ Show Timer...", self)
        timer_act.triggered.connect(self.open_local_timer_dialog)
        menu.addAction(timer_act)

        menu.addSeparator()

        info_toggle_text = "Show Standby Details (IP/PIN)" if self.hide_details else "Hide Standby Details (IP/PIN)"
        toggle_info_act = QAction(info_toggle_text, self)
        toggle_info_act.triggered.connect(self.toggle_details_visibility)
        menu.addAction(toggle_info_act)

        menu.addSeparator()

        if self.isFullScreen():
            fs_act = QAction("Exit Fullscreen (F11)", self)
            fs_act.triggered.connect(self.showNormal)
        else:
            fs_act = QAction("Enter Fullscreen (F11)", self)
            fs_act.triggered.connect(self.showFullScreen)
        menu.addAction(fs_act)

        if self.stack.currentWidget() == self.canvas:
            disc_act = QAction("Disconnect Stream", self)
            disc_act.triggered.connect(self.on_disconnected)
            menu.addAction(disc_act)

        menu.addSeparator()

        exit_act = QAction("Exit Application (Esc)", self)
        exit_act.triggered.connect(self.close)
        menu.addAction(exit_act)

        menu.exec(global_pos)

    def on_connected(self, ip: str):
        self.stack.setCurrentWidget(self.canvas)

    def on_disconnected(self):
        self.canvas.current_frame = None
        self.stack.setCurrentWidget(self.standby)

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
    app = QApplication(sys.argv)
    win = ReceiverMainWindow()
    win.showFullScreen()
    sys.exit(app.exec())