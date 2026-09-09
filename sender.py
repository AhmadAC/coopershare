#################### START OF FILE: sender.py ####################

"""
MrCoopersScreenShare - Sender (PC Presenter & Control Executor)
Features: Auto-Discovery, Robust Auto-Connect, Optional PIN Auth, Taskbar Support,
          60 FPS, Deprecation-free MSS, High-Speed Low Latency Streaming.
"""

import ctypes
import json
import socket
import struct
import sys
import time
from typing import Optional

import cv2
import mss
import numpy as np
from PySide6.QtCore import QPoint, Qt, QThread, Signal
from PySide6.QtGui import QAction, QFont, QIcon
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

# Ensure proper Windows Taskbar Grouping & Icon
if sys.platform == "win32":
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "mrcoopers.screenshare.sender.1"
        )
    except Exception:
        pass


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

    device_found = Signal(str, bool)  # ip, pin_required

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
        quality: int = 65,
        fps_limit: int = 60,
    ):
        super().__init__()
        self.target_ip = target_ip
        self.pin = pin
        self.quality = quality
        self.fps_limit = fps_limit
        self.running = True
        self.paused = False

    def run(self):
        print(f"[DEBUG Sender Video] Connecting to {self.target_ip}:{VIDEO_PORT} (PIN: '{self.pin}')...")
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
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
            print(f"[DEBUG Sender Video] Connected & Authorized. Streaming at {self.fps_limit} FPS...")
            self.status_changed.emit(f"Streaming ({self.fps_limit} FPS)", True)
        except Exception as e:
            print(f"[DEBUG Sender Video] Connection error: {e}")
            self.status_changed.emit(f"Connect Error: {e}", False)
            return

        target_frame_time = 1.0 / max(1, self.fps_limit)

        with create_mss_instance() as sct:
            monitor = sct.monitors[1]
            encode_params = [int(cv2.IMWRITE_JPEG_QUALITY), self.quality]

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
# Floating Frameless Controller UI
# ---------------------------------------------------------------------------


class FloatingSenderWindow(QWidget):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("MrCoopersScreenShare - Sender")
        self.stream_thread: Optional[ScreenSenderThread] = None
        self.audio_thread: Optional[AudioSenderThread] = None
        self.input_thread: Optional[InputReceiverThread] = None

        self._drag_pos = QPoint()
        self.is_expanded = False
        self.is_paused = False
        self.is_muted = False
        self.discovered_ip = ""
        self.pin_required = False

        self._init_window()
        self._setup_ui()

        # Start listening for auto-discovery beacon
        self.discovery_thread = DiscoveryListenerThread()
        self.discovery_thread.device_found.connect(self.on_device_discovered)
        self.discovery_thread.start()

    def _init_window(self):
        self.setWindowFlags(
            Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setWindowOpacity(0.92)

    def _setup_ui(self):
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)

        self.card = QFrame(self)
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
        self.card_layout.setContentsMargins(12, 8, 12, 8)
        self.card_layout.setSpacing(8)

        # Header Pill
        self.header_bar = QWidget()
        h_layout = QHBoxLayout(self.header_bar)
        h_layout.setContentsMargins(0, 0, 0, 0)
        h_layout.setSpacing(6)

        self.status_dot = QLabel("●")
        self.status_dot.setStyleSheet("color: #8f9bb3; font-size: 14px;")
        self.title_lbl = QLabel("MrCoopersScreenShare")
        self.title_lbl.setStyleSheet("font-weight: bold; color: #00a2ed;")

        self.expand_btn = QPushButton("▼")
        self.expand_btn.setFixedSize(24, 24)
        self.expand_btn.setStyleSheet(
            "background: #262c3b; border-radius: 12px; padding: 0px;"
        )
        self.expand_btn.clicked.connect(self.toggle_expand)

        self.close_btn = QPushButton("✕")
        self.close_btn.setFixedSize(24, 24)
        self.close_btn.setStyleSheet(
            "background: #332228; color: #ff6b6b; border-radius: 12px; padding: 0px;"
        )
        self.close_btn.clicked.connect(self.close)

        h_layout.addWidget(self.status_dot)
        h_layout.addWidget(self.title_lbl)
        h_layout.addStretch()
        h_layout.addWidget(self.expand_btn)
        h_layout.addWidget(self.close_btn)

        self.card_layout.addWidget(self.header_bar)

        # Expanded Controls
        self.control_panel = QWidget()
        p_layout = QVBoxLayout(self.control_panel)
        p_layout.setContentsMargins(0, 4, 0, 0)
        p_layout.setSpacing(8)

        # Row 1: Target IP + FPS Selector + Share Button
        ip_row = QHBoxLayout()
        self.ip_input = QLineEdit()
        self.ip_input.setPlaceholderText("Receiver IP (e.g. 192.168.1.5)")

        self.fps_combo = QComboBox()
        self.fps_combo.addItems(["60 FPS", "30 FPS"])
        self.fps_combo.setCurrentIndex(0)

        self.connect_btn = QPushButton("Share")
        self.connect_btn.clicked.connect(self.toggle_connect)

        ip_row.addWidget(self.ip_input)
        ip_row.addWidget(self.fps_combo)
        ip_row.addWidget(self.connect_btn)
        p_layout.addLayout(ip_row)

        # Row 2: Auto-connect toggle + Optional PIN input
        auto_row = QHBoxLayout()
        self.auto_connect_cb = QCheckBox("Auto-Connect")
        self.auto_connect_cb.setChecked(True)

        self.pin_input = QLineEdit()
        self.pin_input.setPlaceholderText("PIN (if required)")
        self.pin_input.setMaxLength(4)
        self.pin_input.setFixedWidth(110)
        self.pin_input.textChanged.connect(self.on_pin_text_changed)

        auto_row.addWidget(self.auto_connect_cb)
        auto_row.addStretch()
        auto_row.addWidget(self.pin_input)
        p_layout.addLayout(auto_row)

        # Row 3: Action Buttons
        btn_row = QHBoxLayout()
        self.pause_btn = QPushButton("⏸ Pause")
        self.pause_btn.clicked.connect(self.toggle_pause)
        self.pause_btn.setEnabled(False)

        self.mute_btn = QPushButton("🔊 Audio On")
        self.mute_btn.clicked.connect(self.toggle_mute)
        self.mute_btn.setEnabled(False)

        btn_row.addWidget(self.pause_btn)
        btn_row.addWidget(self.mute_btn)
        p_layout.addLayout(btn_row)

        # Row 4: Opacity Slider
        trans_row = QHBoxLayout()
        trans_row.addWidget(QLabel("Opacity:"))
        self.opacity_slider = QSlider(Qt.Horizontal)
        self.opacity_slider.setRange(20, 100)
        self.opacity_slider.setValue(92)
        self.opacity_slider.valueChanged.connect(
            lambda v: self.setWindowOpacity(v / 100.0)
        )
        trans_row.addWidget(self.opacity_slider)
        p_layout.addLayout(trans_row)

        self.card_layout.addWidget(self.control_panel)
        self.main_layout.addWidget(self.card)

        self.control_panel.setVisible(False)
        self.adjustSize()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            handle = self.windowHandle()
            if (
                handle
                and hasattr(handle, "startSystemMove")
                and handle.startSystemMove()
            ):
                event.accept()
                return
            self._drag_pos = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton and not self._drag_pos.isNull():
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.setStyleSheet("background-color: #262c3b; color: white;")
        quit_action = QAction("Exit MrCoopersScreenShare", self)
        quit_action.triggered.connect(self.close)
        menu.addAction(quit_action)
        menu.exec(event.globalPos())

    def toggle_expand(self):
        self.is_expanded = not self.is_expanded
        self.control_panel.setVisible(self.is_expanded)
        self.expand_btn.setText("▲" if self.is_expanded else "▼")
        self.adjustSize()

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
        # If user types a complete 4-digit PIN while auto-connect is active, initiate share
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

        print(f"[DEBUG Sender] Starting stream to {target_ip} (FPS: {chosen_fps}, PIN: '{pin_code}')...")

        self.fps_combo.setEnabled(False)
        self.connect_btn.setText("Stop")
        self.connect_btn.setStyleSheet("background-color: #d83b01;")
        self.pause_btn.setEnabled(True)
        self.mute_btn.setEnabled(True)

        self.stream_thread = ScreenSenderThread(
            target_ip=target_ip,
            pin=pin_code,
            quality=65,
            fps_limit=chosen_fps,
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
        self.connect_btn.setText("Share")
        self.connect_btn.setStyleSheet("background-color: #0078d4;")
        self.pause_btn.setEnabled(False)
        self.mute_btn.setEnabled(False)
        self.status_dot.setStyleSheet("color: #8f9bb3; font-size: 14px;")

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
        self.status_dot.setStyleSheet(
            f"color: {'#00d084' if active else '#d83b01'}; font-size: 14px;"
        )
        if not active:
            self.stop_sharing()

    def closeEvent(self, event):
        print("[DEBUG Sender] Application closing. Terminating all active threads...")
        self.stop_sharing()
        if self.discovery_thread:
            self.discovery_thread.stop()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = FloatingSenderWindow()
    win.show()
    win.move(80, 80)
    sys.exit(app.exec())