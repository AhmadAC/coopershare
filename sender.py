#################### START OF FILE: sender.py ####################

"""
MrCoopersScreenShare - Sender (PC Presenter & Control Executor)
Features: Onedir Hot-Replaceable Script Bootstrap, Taskbar Click Toggle,
          Custom Application Icon, Enter Key Screenshare Trigger,
          Save IP to history.json ONLY on Success, Strict Always-On-Top Enforcer,
          Highly-Visible Collapsed Mini Pill (Hover-Illuminated, 30% Base Opacity),
          RDP-Level Quality (4:4:4 Chroma Subsampling, Crisp Text Rendering),
          High-Throughput 2MB TCP Socket, Auto-Discovery, Robust Auto-Connect,
          Optional PIN Auth, 60 FPS.
"""

import ctypes
import json
import os
import runpy
import socket
import struct
import sys
import time
from typing import Optional

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

import cv2
import mss
import numpy as np
from PySide6.QtCore import QEvent, QPoint, Qt, QThread, Signal
from PySide6.QtGui import (
    QAction,
    QColor,
    QCursor,
    QFont,
    QIcon,
    QKeyEvent,
    QPainter,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

# Optional Sound Support
try:
    import sounddevice as sd

    AUDIO_AVAILABLE = True
except Exception as e:
    print(f"[DEBUG Sender Audio] sounddevice unavailable: {e}")
    AUDIO_AVAILABLE = False

# Input Backends (evdev for Wayland / Linux, pynput fallback for Windows)
USE_EVDEV = False
if sys.platform.startswith("linux"):
    try:
        import evdev
        from evdev import AbsInfo, UInput, ecodes as e

        USE_EVDEV = True
    except Exception:
        USE_EVDEV = False

if not USE_EVDEV:
    try:
        from pynput.mouse import Button, Controller as MouseController
    except Exception:
        pass

VIDEO_PORT = 9988
CONTROL_PORT = 9989
AUDIO_PORT = 9990
DISCOVERY_PORT = 9991
SAMPLE_RATE = 44100
CHANNELS = 2
SOCKET_BUFFER_SIZE = 2 * 1024 * 1024  # 2MB High-Fidelity Buffer

# Ensure proper Windows Taskbar Grouping & Icon
if sys.platform == "win32":
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "mrcoopers.screenshare.sender.1"
        )
    except Exception:
        pass


def get_app_directory() -> str:
    """Returns the base directory where sender is running."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


HISTORY_FILE_PATH = os.path.join(get_app_directory(), "history.json")


def load_ip_from_history() -> str:
    """Loads the last saved IP from history.json."""
    if os.path.exists(HISTORY_FILE_PATH):
        try:
            with open(HISTORY_FILE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                return str(data.get("last_ip", "")).strip()
        except Exception as e:
            print(f"[DEBUG Sender] Failed to read history.json: {e}")
    return ""


def save_ip_to_history(ip_address: str):
    """Saves the IP address to history.json upon successful connection."""
    ip_clean = ip_address.strip()
    if not ip_clean:
        return
    try:
        data = {"last_ip": ip_clean}
        with open(HISTORY_FILE_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
        print(f"[DEBUG Sender] Successfully connected! Saved '{ip_clean}' to history.json")
    except Exception as e:
        print(f"[DEBUG Sender] Failed to write history.json: {e}")


def create_application_icon() -> QIcon:
    """Generates a high-DPI desktop and taskbar icon for MrCoopersScreenShare."""
    pix = QPixmap(64, 64)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing, True)

    # Blue Rounded Background Badge
    painter.setBrush(QColor("#0078d4"))
    painter.setPen(Qt.NoPen)
    painter.drawRoundedRect(4, 4, 56, 56, 14, 14)

    # White Screen Frame
    painter.setBrush(QColor("#ffffff"))
    painter.drawRoundedRect(14, 15, 36, 24, 4, 4)

    # Screen Display Area
    painter.setBrush(QColor("#1a1e29"))
    painter.drawRect(18, 19, 28, 16)

    # Screen Stand & Base
    painter.setBrush(QColor("#ffffff"))
    painter.drawRect(29, 41, 6, 4)
    painter.drawRoundedRect(22, 45, 20, 3, 1, 1)

    painter.end()
    return QIcon(pix)


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
    """Returns a mss instance without deprecation warnings."""
    if hasattr(mss, "MSS"):
        return mss.MSS()
    return mss.mss()


# ---------------------------------------------------------------------------
# Cross-Platform Input Injector
# ---------------------------------------------------------------------------


class UniversalInputInjector:

    def __init__(self, screen_w: int, screen_h: int):
        self.screen_w = screen_w
        self.screen_h = screen_h
        self.mode = "none"

        if USE_EVDEV:
            try:
                cap = {
                    e.EV_KEY: [e.BTN_LEFT, e.BTN_RIGHT, e.BTN_MIDDLE],
                    e.EV_ABS: [
                        (
                            e.ABS_X,
                            AbsInfo(
                                value=0,
                                min=0,
                                max=screen_w,
                                fuzz=0,
                                flat=0,
                                resolution=0,
                            ),
                        ),
                        (
                            e.ABS_Y,
                            AbsInfo(
                                value=0,
                                min=0,
                                max=screen_h,
                                fuzz=0,
                                flat=0,
                                resolution=0,
                            ),
                        ),
                    ],
                    e.EV_REL: [e.REL_WHEEL],
                }
                self.ui = UInput(cap, name="mrcoopers-virtual-input")
                self.mode = "evdev"
                print("[DEBUG Injector] Using Linux evdev virtual input.")
            except Exception as ex:
                print(f"[DEBUG Injector] evdev init failed: {ex}")
                self.mode = "none"

        if self.mode == "none":
            try:
                self.mouse = MouseController()
                self.mode = "pynput"
                print("[DEBUG Injector] Using pynput mouse controller.")
            except Exception as ex:
                print(f"[DEBUG Injector] pynput init failed: {ex}")
                self.mode = "unsupported"

    def execute(self, event: dict):
        ev_type = event.get("type")
        nx = event.get("x")
        ny = event.get("y")

        if self.mode == "evdev":
            if nx is not None and ny is not None:
                self.ui.write(e.EV_ABS, e.ABS_X, int(nx * self.screen_w))
                self.ui.write(e.EV_ABS, e.ABS_Y, int(ny * self.screen_h))
            if ev_type in ("touch_down", "mouse_down"):
                btn = (
                    e.BTN_RIGHT
                    if event.get("button") == "right"
                    else e.BTN_LEFT
                )
                self.ui.write(e.EV_KEY, btn, 1)
            elif ev_type in ("touch_up", "mouse_up"):
                btn = (
                    e.BTN_RIGHT
                    if event.get("button") == "right"
                    else e.BTN_LEFT
                )
                self.ui.write(e.EV_KEY, btn, 0)
            elif ev_type == "scroll":
                dy = 1 if event.get("dy", 0) > 0 else -1
                self.ui.write(e.EV_REL, e.REL_WHEEL, dy)
            self.ui.syn()

        elif self.mode == "pynput":
            if nx is not None and ny is not None:
                self.mouse.position = (
                    int(nx * self.screen_w),
                    int(ny * self.screen_h),
                )
            if ev_type in ("touch_down", "mouse_down"):
                btn = (
                    Button.right
                    if event.get("button") == "right"
                    else Button.left
                )
                self.mouse.press(btn)
            elif ev_type in ("touch_up", "mouse_up"):
                btn = (
                    Button.right
                    if event.get("button") == "right"
                    else Button.left
                )
                self.mouse.release(btn)
            elif ev_type == "scroll":
                self.mouse.scroll(0, 1 if event.get("dy", 0) > 0 else -1)

    def close(self):
        if self.mode == "evdev":
            try:
                self.ui.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Background Threads (Discovery, Screen, Audio, Input)
# ---------------------------------------------------------------------------


class DiscoveryListenerThread(QThread):
    """Listens for Receiver beacons on LAN."""

    device_found = Signal(str, bool)

    def __init__(self):
        super().__init__()
        self.running = True

    def run(self):
        print(f"[DEBUG Sender Discovery] Listening for UDP beacons on port {DISCOVERY_PORT}...")
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except Exception:
                pass

        sock.settimeout(1.0)
        try:
            sock.bind(("", DISCOVERY_PORT))
        except Exception as e:
            print(f"[DEBUG Sender Discovery] UDP bind error on port {DISCOVERY_PORT}: {e}")
            return

        while self.running:
            try:
                data, addr = sock.recvfrom(2048)
                payload = json.loads(data.decode("utf-8"))
                if payload.get("service") == "MrCoopersScreenShare":
                    rec_ip = payload.get("ip", addr[0])
                    pin_req = payload.get("pin_required", False)
                    self.device_found.emit(rec_ip, pin_req)
            except socket.timeout:
                continue
            except Exception as e:
                if self.running:
                    print(f"[DEBUG Sender Discovery] Decode error: {e}")
                continue

        sock.close()
        print("[DEBUG Sender Discovery] Discovery listener stopped.")

    def stop(self):
        self.running = False
        self.wait(1000)


class ScreenSenderThread(QThread):
    status_changed = Signal(str, bool)

    def __init__(
        self,
        target_ip: str,
        pin: str = "",
        quality: int = 95,
        fps_limit: int = 60,
        use_444_chroma: bool = True,
    ):
        super().__init__()
        self.target_ip = target_ip
        self.pin = pin
        self.quality = quality
        self.fps_limit = fps_limit
        self.use_444_chroma = use_444_chroma
        self.running = True
        self.paused = False

    def run(self):
        print(
            f"[DEBUG Sender Video] Connecting to {self.target_ip}:{VIDEO_PORT} "
            f"(PIN: '{self.pin}', Quality: {self.quality}, 4:4:4 Chroma: {self.use_444_chroma})..."
        )
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, SOCKET_BUFFER_SIZE)
            except Exception as e:
                print(f"[DEBUG Sender Video] SO_SNDBUF setting notice: {e}")

            sock.settimeout(4.0)
            sock.connect((self.target_ip, VIDEO_PORT))

            # Handshake with PIN
            handshake = json.dumps({"pin": self.pin}).encode("utf-8")
            sock.sendall(struct.pack(">L", len(handshake)) + handshake)

            # Wait for handshake response
            resp_raw = recv_exact(sock, 4)
            if not resp_raw:
                raise ConnectionError("Server rejected connection or closed socket.")

            resp_len = struct.unpack(">L", resp_raw)[0]
            resp_bytes = recv_exact(sock, resp_len)
            if not resp_bytes:
                raise ConnectionError("Failed to receive authentication response.")

            resp = json.loads(resp_bytes.decode("utf-8"))
            print(f"[DEBUG Sender Video] Handshake response: {resp}")

            if not resp.get("auth", False):
                err_msg = resp.get("msg", "Auth Failed")
                print(f"[DEBUG Sender Video] Auth rejected: {err_msg}")
                self.status_changed.emit(f"Error: {err_msg}", False)
                sock.close()
                return

            sock.settimeout(None)
            print(f"[DEBUG Sender Video] Connected & Authorized. Streaming at {self.fps_limit} FPS (RDP Ultra Quality)...")
            self.status_changed.emit(f"Streaming ({self.fps_limit} FPS)", True)
        except Exception as e:
            print(f"[DEBUG Sender Video] Connection error: {e}")
            self.status_changed.emit(f"Connect Error: {e}", False)
            return

        target_frame_time = 1.0 / max(1, self.fps_limit)

        # Build High-Fidelity RDP-Level Encoding Parameters
        encode_params = [int(cv2.IMWRITE_JPEG_QUALITY), self.quality]

        if hasattr(cv2, "IMWRITE_JPEG_OPTIMIZE"):
            encode_params.extend([int(cv2.IMWRITE_JPEG_OPTIMIZE), 1])

        if self.use_444_chroma:
            sampling_factor_id = getattr(cv2, "IMWRITE_JPEG_SAMPLING_FACTOR", 10)
            sampling_444_val = getattr(cv2, "IMWRITE_JPEG_SAMPLING_FACTOR_444", 0x00010001)
            encode_params.extend([int(sampling_factor_id), int(sampling_444_val)])

        with create_mss_instance() as sct:
            monitor = sct.monitors[1]

            while self.running:
                t_start = time.perf_counter()

                if self.paused:
                    self.msleep(100)
                    continue

                img = np.array(sct.grab(monitor))
                bgr = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
                success, enc_img = cv2.imencode(".jpg", bgr, encode_params)

                if success:
                    data = enc_img.tobytes()
                    try:
                        sock.sendall(struct.pack(">L", len(data)) + data)
                    except Exception as e:
                        print(f"[DEBUG Sender Video] Frame send failed: {e}")
                        break

                elapsed = time.perf_counter() - t_start
                sleep_sec = target_frame_time - elapsed
                if sleep_sec > 0:
                    self.msleep(int(sleep_sec * 1000))

        try:
            sock.close()
        except Exception:
            pass

        print("[DEBUG Sender Video] Video streaming thread stopped.")
        self.status_changed.emit("Disconnected", False)

    def stop(self):
        self.running = False
        self.wait(1000)


class AudioSenderThread(QThread):
    def __init__(self, target_ip: str):
        super().__init__()
        self.target_ip = target_ip
        self.running = True
        self.muted = False
        self.sock: Optional[socket.socket] = None

    def run(self):
        if not AUDIO_AVAILABLE:
            return
        print(f"[DEBUG Sender Audio] Connecting to {self.target_ip}:{AUDIO_PORT}...")
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.sock.settimeout(3.0)
            self.sock.connect((self.target_ip, AUDIO_PORT))
            self.sock.settimeout(None)
            print("[DEBUG Sender Audio] Connected to audio receiver.")
        except Exception as e:
            print(f"[DEBUG Sender Audio] Connection failed: {e}")
            return

        def callback(indata, frames, time_info, status):
            if self.running and not self.muted and self.sock:
                try:
                    self.sock.sendall(indata.tobytes())
                except Exception:
                    pass

        try:
            with sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype="int16",
                callback=callback,
            ):
                while self.running:
                    self.msleep(100)
        except Exception as e:
            print(f"[DEBUG Sender Audio] InputStream error: {e}")

        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
        print("[DEBUG Sender Audio] Audio sender stopped.")

    def stop(self):
        self.running = False
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
        self.wait(1000)


class InputReceiverThread(QThread):
    def __init__(self, target_ip: str):
        super().__init__()
        self.target_ip = target_ip
        self.running = True

    def run(self):
        print(f"[DEBUG Sender Control] Connecting to {self.target_ip}:{CONTROL_PORT}...")
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.settimeout(3.0)
            sock.connect((self.target_ip, CONTROL_PORT))
            sock.settimeout(0.5)
            print("[DEBUG Sender Control] Control channel connected.")
        except Exception as e:
            print(f"[DEBUG Sender Control] Control channel connection failed: {e}")
            return

        with create_mss_instance() as sct:
            mon = sct.monitors[1]
            scr_w, scr_h = mon["width"], mon["height"]

        injector = UniversalInputInjector(scr_w, scr_h)
        payload_size = struct.calcsize(">L")
        data = bytearray()

        while self.running:
            try:
                while len(data) < payload_size:
                    if not self.running:
                        break
                    try:
                        packet = sock.recv(2048)
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
                        packet = sock.recv(min(msg_size - len(data), 4096))
                        if not packet:
                            raise ConnectionResetError
                        data.extend(packet)
                    except socket.timeout:
                        continue

                if not self.running:
                    break

                raw_msg = data[:msg_size]
                data = data[msg_size:]
                event = json.loads(raw_msg.decode("utf-8"))
                injector.execute(event)
            except ConnectionResetError:
                break
            except Exception as e:
                if self.running:
                    print(f"[DEBUG Sender Control] Input processing error: {e}")
                break

        injector.close()
        try:
            sock.close()
        except Exception:
            pass
        print("[DEBUG Sender Control] Input receiver thread stopped.")

    def stop(self):
        self.running = False
        self.wait(1000)


# ---------------------------------------------------------------------------
# Floating Frameless Controller UI (Taskbar Toggle & Always-On-Top Mini Pill)
# ---------------------------------------------------------------------------


class FloatingSenderWindow(QWidget):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("MrCoopersScreenShare - Sender")
        self.setWindowIcon(create_application_icon())

        self.stream_thread: Optional[ScreenSenderThread] = None
        self.audio_thread: Optional[AudioSenderThread] = None
        self.input_thread: Optional[InputReceiverThread] = None

        self._drag_start_pos = QPoint()
        self._window_start_pos = QPoint()
        self._is_dragging = False

        self.is_mini_mode = False
        self.is_paused = False
        self.is_muted = False
        self.discovered_ip = ""
        self.pin_required = False
        self.status_color = "#8f9bb3"

        self._init_window()
        self._setup_ui()
        self._load_saved_history()

        # Start listening for auto-discovery beacon
        self.discovery_thread = DiscoveryListenerThread()
        self.discovery_thread.device_found.connect(self.on_device_discovered)
        self.discovery_thread.start()

    def _init_window(self):
        self.setWindowFlags(
            Qt.Window
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.WindowMinimizeButtonHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setWindowOpacity(0.94)

        if sys.platform == "win32":
            try:
                hwnd = int(self.winId())
                GWL_STYLE = -16
                WS_MINIMIZEBOX = 0x00020000
                WS_SYSMENU = 0x00080000
                style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_STYLE)
                ctypes.windll.user32.SetWindowLongW(
                    hwnd, GWL_STYLE, style | WS_MINIMIZEBOX | WS_SYSMENU
                )
            except Exception as e:
                print(f"[DEBUG Sender Win32] Style init notice: {e}")

        self.enforce_always_on_top()

    def enforce_always_on_top(self):
        """Hardware/Win32 OS level reinforcement to maintain topmost z-order."""
        if sys.platform == "win32":
            try:
                hwnd = int(self.winId())
                HWND_TOPMOST = -1
                SWP_NOMOVE = 0x0002
                SWP_NOSIZE = 0x0001
                SWP_NOACTIVATE = 0x0010
                SWP_SHOWWINDOW = 0x0040
                ctypes.windll.user32.SetWindowPos(
                    hwnd,
                    HWND_TOPMOST,
                    0,
                    0,
                    0,
                    0,
                    SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW,
                )
            except Exception:
                pass
        self.raise_()

    def changeEvent(self, event: QEvent):
        """Handles taskbar minimize and restore state toggling cleanly."""
        if event.type() == QEvent.WindowStateChange:
            if not self.isMinimized():
                self.enforce_always_on_top()
                if not self.is_mini_mode:
                    self.setWindowOpacity(self.opacity_slider.value() / 100.0)
                else:
                    self.setWindowOpacity(0.35)
        super().changeEvent(event)

    def _setup_ui(self):
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        self.stack = QStackedWidget(self)

        # -------------------------------------------------------------------
        # View 1: Expanded Full Controller Card
        # -------------------------------------------------------------------
        self.card = QFrame()
        self.card.setObjectName("card")
        self.card.setStyleSheet(
            """
            QFrame#card {
                background-color: #1a1e29;
                border: 1px solid #333c4d;
                border-radius: 14px;
            }
            QLabel { color: #ffffff; font-family: 'Segoe UI', sans-serif; font-size: 12px; }
            QLineEdit, QComboBox {
                background: #262c3b; border: 1px solid #3d475f;
                color: #ffffff; border-radius: 6px; padding: 5px 8px; font-size: 12px;
            }
            QPushButton {
                background-color: #0078d4; color: white; border: none;
                border-radius: 6px; font-weight: bold; font-size: 11px; padding: 6px 12px;
            }
            QPushButton:hover { background-color: #106ebe; }
            QCheckBox { color: #8f9bb3; font-size: 11px; }
            QSlider::groove:horizontal { height: 4px; background: #333c4e; border-radius: 2px; }
            QSlider::handle:horizontal { background: #00a2ed; width: 12px; margin: -4px 0; border-radius: 6px; }
        """
        )
        self.card_layout = QVBoxLayout(self.card)
        self.card_layout.setContentsMargins(12, 10, 12, 10)
        self.card_layout.setSpacing(8)

        # Header Pill
        self.header_bar = QWidget()
        h_layout = QHBoxLayout(self.header_bar)
        h_layout.setContentsMargins(0, 0, 0, 0)
        h_layout.setSpacing(6)

        self.status_dot = QLabel("●")
        self.status_dot.setStyleSheet(f"color: {self.status_color}; font-size: 14px;")
        self.title_lbl = QLabel("MrCoopersScreenShare")
        self.title_lbl.setStyleSheet("font-weight: bold; color: #00a2ed;")

        self.collapse_btn = QPushButton("▼")
        self.collapse_btn.setToolTip("Collapse to mini floating indicator")
        self.collapse_btn.setFixedSize(24, 24)
        self.collapse_btn.setStyleSheet(
            "background: #262c3b; border-radius: 12px; padding: 0px;"
        )
        self.collapse_btn.clicked.connect(self.collapse_to_mini)

        self.close_btn = QPushButton("✕")
        self.close_btn.setFixedSize(24, 24)
        self.close_btn.setStyleSheet(
            "background: #332228; color: #ff6b6b; border-radius: 12px; padding: 0px;"
        )
        self.close_btn.clicked.connect(self.close)

        h_layout.addWidget(self.status_dot)
        h_layout.addWidget(self.title_lbl)
        h_layout.addStretch()
        h_layout.addWidget(self.collapse_btn)
        h_layout.addWidget(self.close_btn)

        self.card_layout.addWidget(self.header_bar)

        # Controls
        # Row 1: Target IP + Share Button
        ip_row = QHBoxLayout()
        self.ip_input = QLineEdit()
        self.ip_input.setPlaceholderText("Receiver IP (e.g. 192.168.1.5)")
        self.ip_input.returnPressed.connect(self.toggle_connect)

        self.connect_btn = QPushButton("Share")
        self.connect_btn.clicked.connect(self.toggle_connect)

        ip_row.addWidget(self.ip_input)
        ip_row.addWidget(self.connect_btn)
        self.card_layout.addLayout(ip_row)

        # Row 2: FPS & RDP-Quality Preset Selector
        qual_row = QHBoxLayout()
        self.fps_combo = QComboBox()
        self.fps_combo.addItems(["60 FPS", "30 FPS"])
        self.fps_combo.setCurrentIndex(0)

        self.quality_combo = QComboBox()
        self.quality_combo.addItems(["Ultra (RDP 4:4:4)", "High Quality (85)", "Balanced (70)"])
        self.quality_combo.setCurrentIndex(0)
        self.quality_combo.setToolTip("Ultra uses full 4:4:4 chroma subsampling for crisp RDP-tier text.")

        qual_row.addWidget(self.fps_combo)
        qual_row.addWidget(self.quality_combo)
        self.card_layout.addLayout(qual_row)

        # Row 3: Auto-connect toggle + Optional PIN input
        auto_row = QHBoxLayout()
        self.auto_connect_cb = QCheckBox("Auto-Connect")
        self.auto_connect_cb.setChecked(True)

        self.pin_input = QLineEdit()
        self.pin_input.setPlaceholderText("PIN (if required)")
        self.pin_input.setMaxLength(4)
        self.pin_input.setFixedWidth(110)
        self.pin_input.returnPressed.connect(self.toggle_connect)
        self.pin_input.textChanged.connect(self.on_pin_text_changed)

        auto_row.addWidget(self.auto_connect_cb)
        auto_row.addStretch()
        auto_row.addWidget(self.pin_input)
        self.card_layout.addLayout(auto_row)

        # Row 4: Action Buttons
        btn_row = QHBoxLayout()
        self.pause_btn = QPushButton("⏸ Pause")
        self.pause_btn.clicked.connect(self.toggle_pause)
        self.pause_btn.setEnabled(False)

        self.mute_btn = QPushButton("🔊 Audio On")
        self.mute_btn.clicked.connect(self.toggle_mute)
        self.mute_btn.setEnabled(False)

        btn_row.addWidget(self.pause_btn)
        btn_row.addWidget(self.mute_btn)
        self.card_layout.addLayout(btn_row)

        # Row 5: Opacity Slider
        trans_row = QHBoxLayout()
        trans_row.addWidget(QLabel("Opacity:"))
        self.opacity_slider = QSlider(Qt.Horizontal)
        self.opacity_slider.setRange(20, 100)
        self.opacity_slider.setValue(94)
        self.opacity_slider.valueChanged.connect(
            lambda v: self.setWindowOpacity(v / 100.0) if not self.is_mini_mode else None
        )
        trans_row.addWidget(self.opacity_slider)
        self.card_layout.addLayout(trans_row)

        # -------------------------------------------------------------------
        # View 2: Collapsed Mini Pill Indicator (48x16 Hitbox, 30x5 Rounded Bar)
        # -------------------------------------------------------------------
        self.mini_container = QFrame()
        self.mini_container.setObjectName("mini_container")
        self.mini_container.setFixedSize(48, 16)
        self.mini_container.setCursor(Qt.PointingHandCursor)
        self.mini_container.setToolTip("MrCoopersScreenShare (Always On Top | Click to expand / Drag to move)")

        mini_layout = QVBoxLayout(self.mini_container)
        mini_layout.setContentsMargins(0, 0, 0, 0)
        mini_layout.setAlignment(Qt.AlignCenter)

        self.mini_bar = QFrame()
        self.mini_bar.setObjectName("mini_bar")
        self.mini_bar.setFixedSize(30, 5)
        self._update_mini_bar_style()

        mini_layout.addWidget(self.mini_bar)

        self.stack.addWidget(self.card)
        self.stack.addWidget(self.mini_container)

        self.main_layout.addWidget(self.stack)
        self.expand_window()

    def _load_saved_history(self):
        saved_ip = load_ip_from_history()
        if saved_ip:
            self.ip_input.setText(saved_ip)
            print(f"[DEBUG Sender] Auto-filled saved IP: {saved_ip}")

    def _update_mini_bar_style(self):
        self.mini_container.setStyleSheet(
            """
            QFrame#mini_container {
                background: transparent;
            }
        """
        )
        self.mini_bar.setStyleSheet(
            f"""
            QFrame#mini_bar {{
                background-color: {self.status_color};
                border: 1px solid rgba(255, 255, 255, 0.45);
                border-radius: 2px;
            }}
            QFrame#mini_bar:hover {{
                background-color: #00a2ed;
                border: 1px solid #ffffff;
            }}
        """
        )

    def collapse_to_mini(self):
        self.is_mini_mode = True
        self.stack.setCurrentWidget(self.mini_container)
        self.setWindowOpacity(0.35)
        self.setFixedSize(48, 16)
        self.enforce_always_on_top()
        print("[DEBUG Sender GUI] Collapsed to always-on-top mini floating indicator.")

    def expand_window(self):
        self.is_mini_mode = False
        self.setMinimumSize(0, 0)
        self.setMaximumSize(16777215, 16777215)
        self.stack.setCurrentWidget(self.card)
        self.setWindowOpacity(self.opacity_slider.value() / 100.0)
        self.card.adjustSize()
        self.adjustSize()
        self.enforce_always_on_top()
        print("[DEBUG Sender GUI] Expanded to full always-on-top controller card.")

    # -----------------------------------------------------------------------
    # Keyboard & Mouse Events
    # -----------------------------------------------------------------------
    def keyPressEvent(self, event: QKeyEvent):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if not self.is_mini_mode:
                self.toggle_connect()
                event.accept()
                return
        super().keyPressEvent(event)

    def enterEvent(self, event):
        if self.is_mini_mode:
            self.setWindowOpacity(1.0)
        self.enforce_always_on_top()
        super().enterEvent(event)

    def leaveEvent(self, event):
        if self.is_mini_mode:
            self.setWindowOpacity(0.35)
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.enforce_always_on_top()
            self._drag_start_pos = event.globalPosition().toPoint()
            self._window_start_pos = self.pos()
            self._is_dragging = False

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton:
            diff = event.globalPosition().toPoint() - self._drag_start_pos
            if diff.manhattanLength() > 2:
                self._is_dragging = True
                self.move(self._window_start_pos + diff)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            if self.is_mini_mode and not self._is_dragging:
                self.expand_window()
            self.enforce_always_on_top()

    def showEvent(self, event):
        self.enforce_always_on_top()
        super().showEvent(event)

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.setStyleSheet("background-color: #262c3b; color: white;")

        if self.is_mini_mode:
            expand_act = QAction("Expand Controls", self)
            expand_act.triggered.connect(self.expand_window)
            menu.addAction(expand_act)
        else:
            collapse_act = QAction("Collapse to Mini", self)
            collapse_act.triggered.connect(self.collapse_to_mini)
            menu.addAction(collapse_act)

        menu.addSeparator()
        quit_action = QAction("Exit MrCoopersScreenShare", self)
        quit_action.triggered.connect(self.close)
        menu.addAction(quit_action)
        menu.exec(event.globalPos())

    # -----------------------------------------------------------------------
    # Streaming & Connection Logic
    # -----------------------------------------------------------------------
    def on_device_discovered(self, ip: str, pin_required: bool):
        self.discovered_ip = ip
        self.pin_required = pin_required

        if not self.ip_input.text().strip():
            self.ip_input.setText(ip)

        # Trigger auto-connect if enabled and not already streaming
        if (
            self.auto_connect_cb.isChecked()
            and (not self.stream_thread or not self.stream_thread.isRunning())
        ):
            if not pin_required or len(self.pin_input.text().strip()) == 4:
                print(f"[DEBUG Sender] Auto-Connecting to discovered receiver {ip}...")
                self.start_sharing()

    def on_pin_text_changed(self, text: str):
        if (
            len(text.strip()) == 4
            and self.auto_connect_cb.isChecked()
            and (not self.stream_thread or not self.stream_thread.isRunning())
        ):
            print("[DEBUG Sender] 4-Digit PIN entered. Triggering share...")
            self.start_sharing()

    def toggle_connect(self):
        if self.stream_thread and self.stream_thread.isRunning():
            self.stop_sharing()
        else:
            self.start_sharing()

    def start_sharing(self):
        target_ip = self.ip_input.text().strip() or self.discovered_ip
        if not target_ip:
            print("[DEBUG Sender] Cannot start sharing: No target IP provided.")
            return

        chosen_fps = 60 if self.fps_combo.currentIndex() == 0 else 30
        pin_code = self.pin_input.text().strip()

        # Quality Preset Mapping
        quality_idx = self.quality_combo.currentIndex()
        if quality_idx == 0:
            target_quality = 95
            use_444 = True
        elif quality_idx == 1:
            target_quality = 85
            use_444 = True
        else:
            target_quality = 70
            use_444 = False

        print(
            f"[DEBUG Sender] Starting stream to {target_ip} (FPS: {chosen_fps}, "
            f"Quality: {target_quality}, 4:4:4 Chroma: {use_444}, PIN: '{pin_code}')..."
        )

        self.fps_combo.setEnabled(False)
        self.quality_combo.setEnabled(False)
        self.connect_btn.setText("Stop")
        self.connect_btn.setStyleSheet("background-color: #d83b01;")
        self.pause_btn.setEnabled(True)
        self.mute_btn.setEnabled(True)

        self.stream_thread = ScreenSenderThread(
            target_ip=target_ip,
            pin=pin_code,
            quality=target_quality,
            fps_limit=chosen_fps,
            use_444_chroma=use_444,
        )
        self.stream_thread.status_changed.connect(self.on_stream_status)
        self.stream_thread.start()

        self.audio_thread = AudioSenderThread(target_ip)
        self.audio_thread.start()

        self.input_thread = InputReceiverThread(target_ip)
        self.input_thread.start()

    def stop_sharing(self):
        print("[DEBUG Sender] Stopping all sharing threads...")
        for th in (self.stream_thread, self.audio_thread, self.input_thread):
            if th:
                th.stop()
        self.stream_thread = None
        self.audio_thread = None
        self.input_thread = None

        self.fps_combo.setEnabled(True)
        self.quality_combo.setEnabled(True)
        self.connect_btn.setText("Share")
        self.connect_btn.setStyleSheet("background-color: #0078d4;")
        self.pause_btn.setEnabled(False)
        self.mute_btn.setEnabled(False)

        self._update_status_color("#8f9bb3")

    def toggle_pause(self):
        if self.stream_thread:
            self.is_paused = not self.is_paused
            self.stream_thread.paused = self.is_paused
            self.pause_btn.setText("▶ Resume" if self.is_paused else "⏸ Pause")
            print(f"[DEBUG Sender] Screen pause state: {self.is_paused}")

    def toggle_mute(self):
        if self.audio_thread:
            self.is_muted = not self.is_muted
            self.audio_thread.muted = self.is_muted
            self.mute_btn.setText("🔇 Muted" if self.is_muted else "🔊 Audio On")
            print(f"[DEBUG Sender] Audio mute state: {self.is_muted}")

    def on_stream_status(self, text: str, active: bool):
        print(f"[DEBUG Sender] Stream status updated: '{text}' (active={active})")
        color = "#00d084" if active else "#d83b01"
        self._update_status_color(color)

        if active:
            connected_ip = self.ip_input.text().strip() or self.discovered_ip
            if connected_ip:
                save_ip_to_history(connected_ip)
        else:
            self.stop_sharing()

    def _update_status_color(self, color_hex: str):
        self.status_color = color_hex
        self.status_dot.setStyleSheet(f"color: {color_hex}; font-size: 14px;")
        self._update_mini_bar_style()

    def closeEvent(self, event):
        print("[DEBUG Sender] Application closing. Terminating all active threads...")
        self.stop_sharing()
        if self.discovery_thread:
            self.discovery_thread.stop()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setWindowIcon(create_application_icon())
    win = FloatingSenderWindow()
    win.show()
    win.move(80, 80)
    sys.exit(app.exec())