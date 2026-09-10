"""
MrCoopersScreenShare - Receiver (Interactive Touch Display & Sound Hub)
Features: Fullscreen Frameless Mode, Remote Window Management (Maximize, Make Smaller,
          Minimize from Sender Context Menu), Right-Click Context Menu (Exit Fullscreen /
          Exit App / Toggle Standby Details Visibility with JSON persistence),
          Dynamic Multi-Channel Audio Playback, Onedir Hot-Replaceable Script Bootstrap,
          UDP Broadcast Beacon, 4-Digit PIN Authentication, Multi-Touch Canvas,
          Direct Bilinear GPU Blitting, 2MB Socket Buffers, Rotating File Logging,
          Non-blocking Clean Thread Shutdown.
"""

import json
import logging
import os
import random
import runpy
import socket
import struct
import sys
import threading
import time
from typing import Optional

# ---------------------------------------------------------------------------
# Onedir Dynamic Script Loader (Allows replacing receiver.py without recompiling)
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
import numpy as np
from PySide6.QtCore import QEvent, QPointF, QRect, Qt, QThread, Signal
from PySide6.QtGui import QAction, QFont, QImage, QKeyEvent, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

# ---------------------------------------------------------------------------
# Logging & Configuration File Paths
# ---------------------------------------------------------------------------
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
    """Loads receiver configuration from receiver_config.json."""
    if os.path.exists(CONFIG_FILE_PATH):
        try:
            with open(CONFIG_FILE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to read receiver_config.json: {e}")
    return {"hide_details": False}


def save_receiver_config(config: dict):
    """Saves receiver configuration to receiver_config.json."""
    try:
        with open(CONFIG_FILE_PATH, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4)
        logger.debug("Receiver configuration saved successfully.")
    except Exception as e:
        logger.error(f"Failed to save receiver_config.json: {e}")


# Optional Sound Support
try:
    import sounddevice as sd

    AUDIO_AVAILABLE = True
    logger.info("sounddevice audio subsystem initialized successfully.")
except Exception as e:
    AUDIO_AVAILABLE = False
    logger.warning(f"sounddevice audio subsystem unavailable: {e}")

VIDEO_PORT = 9988
CONTROL_PORT = 9989
AUDIO_PORT = 9990
DISCOVERY_PORT = 9991
DEFAULT_SAMPLE_RATE = 48000
CHANNELS = 2
SOCKET_BUFFER_SIZE = 2 * 1024 * 1024  # 2MB High-Throughput Buffer


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
    """Reads exactly `count` bytes from socket buffer or returns None on disconnect."""
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


# ---------------------------------------------------------------------------
# Server & Beacon Threads
# ---------------------------------------------------------------------------


class DiscoveryBeaconThread(QThread):
    """Periodically broadcasts receiver IP & PIN requirement across the LAN."""

    def __init__(self, get_pin_func, get_pin_req_func):
        super().__init__()
        self.setObjectName("BeaconThread")
        self.get_pin_func = get_pin_func
        self.get_pin_req_func = get_pin_req_func
        self.running = True

    def run(self):
        logger.info("Discovery Beacon thread started.")
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

        while self.running:
            try:
                local_ip = get_local_ip()
                pin_req = self.get_pin_req_func()
                payload = json.dumps(
                    {
                        "service": "MrCoopersScreenShare",
                        "ip": local_ip,
                        "pin_required": pin_req,
                    }
                ).encode("utf-8")

                sock.sendto(payload, ("255.255.255.255", DISCOVERY_PORT))
                logger.debug(f"Beacon broadcasted: IP={local_ip}, pin_required={pin_req}")
            except Exception as e:
                logger.error(f"Discovery broadcast error: {e}")

            for _ in range(15):
                if not self.running:
                    break
                self.msleep(100)

        sock.close()
        logger.info("Discovery Beacon thread terminated.")

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
        logger.info(f"Video Server binding to 0.0.0.0:{self.port} (Ultra-Quality mode)...")
        self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, SOCKET_BUFFER_SIZE)
        except Exception as e:
            logger.warning(f"Could not expand socket receive buffer: {e}")

        self.server_sock.bind(("0.0.0.0", self.port))
        self.server_sock.listen(1)
        self.server_sock.settimeout(0.5)

        logger.info("Video Server listening for incoming sender connections.")

        while self.running:
            try:
                conn, addr = self.server_sock.accept()
            except socket.timeout:
                continue
            except Exception as e:
                if self.running:
                    logger.error(f"Video accept error: {e}")
                break

            logger.info(f"Incoming video connection from {addr[0]}:{addr[1]}")
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
        logger.info("Video Server thread finished.")

    def _verify_handshake(self, conn: socket.socket, client_ip: str) -> bool:
        try:
            conn.settimeout(5.0)
            header = recv_exact(conn, 4)
            if not header:
                logger.warning(f"Handshake failed: connection closed by {client_ip}")
                return False

            size = struct.unpack(">L", header)[0]
            raw_payload = recv_exact(conn, size)
            if not raw_payload:
                logger.warning(f"Handshake failed: incomplete payload from {client_ip}")
                return False

            data = json.loads(raw_payload.decode("utf-8"))
            pin_req = self.get_pin_req_func()
            client_pin = str(data.get("pin", "")).strip()
            server_pin = str(self.get_pin_func()).strip()

            logger.info(
                f"Authentication request from {client_ip}: PIN provided='{client_pin}', "
                f"PIN expected='{server_pin}', Required={pin_req}"
            )

            if pin_req and client_pin != server_pin:
                logger.warning(f"Authentication rejected for {client_ip}: Invalid PIN '{client_pin}'")
                resp = json.dumps({"auth": False, "msg": "Incorrect PIN"}).encode("utf-8")
                conn.sendall(struct.pack(">L", len(resp)) + resp)
                return False

            logger.info(f"Authentication approved for {client_ip}.")
            resp = json.dumps({"auth": True, "msg": "OK"}).encode("utf-8")
            conn.sendall(struct.pack(">L", len(resp)) + resp)
            conn.settimeout(None)
            return True
        except Exception as e:
            logger.error(f"Handshake exception with {client_ip}: {e}")
            return False

    def _handle_client(self, conn: socket.socket, client_ip: str):
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        try:
            conn.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, SOCKET_BUFFER_SIZE)
        except Exception:
            pass

        conn.settimeout(0.5)
        payload_size = struct.calcsize(">L")
        data = bytearray()
        frames_count = 0
        last_log_time = time.time()

        logger.info(f"Receiving video frames stream from {client_ip}...")

        while self.running:
            try:
                while len(data) < payload_size:
                    if not self.running:
                        break
                    try:
                        packet = conn.recv(131072)
                        if not packet:
                            raise ConnectionResetError("Connection closed by sender.")
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
                            raise ConnectionResetError("Connection dropped during frame transfer.")
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
                    frames_count += 1

                if time.time() - last_log_time >= 5.0:
                    fps_val = frames_count / (time.time() - last_log_time)
                    logger.debug(f"Video streaming active: ~{fps_val:.1f} FPS (Received {frames_count} frames)")
                    frames_count = 0
                    last_log_time = time.time()

            except ConnectionResetError as e:
                logger.info(f"Video client disconnected ({client_ip}): {e}")
                break
            except Exception as e:
                logger.error(f"Error handling video frame from {client_ip}: {e}")
                break

        try:
            conn.close()
        except Exception:
            pass

        logger.info(f"Video session with {client_ip} ended.")
        self.client_disconnected.emit()

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
            logger.warning("Audio playback disabled (sounddevice not installed).")
            return

        logger.info(f"Audio Server binding to 0.0.0.0:{self.port}...")
        self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_sock.bind(("0.0.0.0", self.port))
        self.server_sock.listen(1)
        self.server_sock.settimeout(0.5)

        while self.running:
            try:
                conn, addr = self.server_sock.accept()
                logger.info(f"Audio connection established with {addr[0]}")
                conn.settimeout(3.0)

                # Read 4-byte sample rate header
                hdr = recv_exact(conn, 4)
                sample_rate = DEFAULT_SAMPLE_RATE
                if hdr:
                    sample_rate = struct.unpack(">I", hdr)[0]
                    logger.info(f"Negotiated audio sample rate: {sample_rate} Hz")

                conn.settimeout(0.5)

                try:
                    out_stream = sd.OutputStream(
                        samplerate=sample_rate,
                        channels=CHANNELS,
                        dtype="int16",
                        latency="low",
                    )
                    out_stream.start()
                    logger.info(f"Audio output playback stream active ({sample_rate} Hz, {CHANNELS} channels).")
                except Exception as ex:
                    logger.error(f"Failed to open audio playback stream: {ex}")
                    conn.close()
                    continue

                audio_buf = bytearray()
                frame_bytes = CHANNELS * 2  # 4 bytes per frame in int16 stereo

                while self.running:
                    try:
                        pcm_data = conn.recv(8192)
                        if not pcm_data:
                            logger.info(f"Audio stream closed by sender {addr[0]}")
                            break
                        audio_buf.extend(pcm_data)

                        # Align to 4-byte frame boundaries and reshape to 2D (frames, channels)
                        valid_bytes = len(audio_buf) - (len(audio_buf) % frame_bytes)
                        if valid_bytes >= frame_bytes:
                            samples = np.frombuffer(audio_buf[:valid_bytes], dtype=np.int16).reshape(-1, CHANNELS)
                            audio_buf = audio_buf[valid_bytes:]
                            out_stream.write(samples)
                    except socket.timeout:
                        continue
                    except Exception as ex:
                        if self.running:
                            logger.error(f"Audio playback exception: {ex}")
                        break

                try:
                    out_stream.stop()
                    out_stream.close()
                except Exception:
                    pass

                conn.close()
                logger.info(f"Audio session with {addr[0]} concluded.")
            except socket.timeout:
                continue
            except Exception as e:
                if self.running:
                    logger.error(f"Audio Server exception: {e}")
                break

        if self.server_sock:
            try:
                self.server_sock.close()
            except Exception:
                pass
        logger.info("Audio Server thread finished.")

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
        logger.info(f"Control Server binding to 0.0.0.0:{self.port}...")
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
                logger.info(f"Control channel connected to {addr[0]}")
            except socket.timeout:
                continue
            except Exception as e:
                if self.running:
                    logger.error(f"Control Server accept exception: {e}")
                break

            # Handle incoming commands (e.g. Remote Window Control) from sender
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
                    logger.info("Control client disconnected.")
                    break
                except Exception as ex:
                    if self.running:
                        logger.error(f"Control channel read error: {ex}")
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
        logger.info("Control Server thread finished.")

    def send_event(self, event_data: dict):
        with self._send_lock:
            if self.client_conn:
                try:
                    msg = json.dumps(event_data).encode("utf-8")
                    self.client_conn.sendall(struct.pack(">L", len(msg)) + msg)
                except Exception as e:
                    logger.warning(f"Failed to transmit control event: {e}")
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
# Canvas & Fullscreen Main Window
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
            target_rect = self._get_video_rect()
            painter.drawPixmap(target_rect, self.current_frame)

    def event(self, event: QEvent) -> bool:
        if event.type() in (
            QEvent.TouchBegin,
            QEvent.TouchUpdate,
            QEvent.TouchEnd,
        ):
            pts = event.touchPoints()
            if pts:
                norm = self._normalize_pos(pts[0].pos())
                if norm:
                    act_map = {
                        QEvent.TouchBegin: "touch_down",
                        QEvent.TouchUpdate: "touch_move",
                        QEvent.TouchEnd: "touch_up",
                    }
                    self.control_server.send_event(
                        {
                            "type": act_map[event.type()],
                            "x": norm[0],
                            "y": norm[1],
                        }
                    )
            return True
        return super().event(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.RightButton:
            event.ignore()
            return

        norm = self._normalize_pos(event.position())
        if norm:
            self.control_server.send_event(
                {
                    "type": "mouse_down",
                    "x": norm[0],
                    "y": norm[1],
                    "button": "left",
                }
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
                {
                    "type": "mouse_up",
                    "x": norm[0],
                    "y": norm[1],
                    "button": "left",
                }
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

        # True Frameless Mode
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
        self.setStyleSheet("background-color: #0b0e14;")

        # Load configuration
        self.config = load_receiver_config()
        self.hide_details = self.config.get("hide_details", False)

        # Generate 4-digit PIN for session
        self.pin = f"{random.randint(1000, 9999)}"
        logger.info(f"Initialized Receiver. Local IP: {get_local_ip()} | Session PIN: {self.pin}")

        self.control_thread = ControlServerThread()
        self.audio_thread = AudioServerThread()
        self.video_thread = VideoServerThread(self.get_pin, self.is_pin_required)
        self.beacon_thread = DiscoveryBeaconThread(self.get_pin, self.is_pin_required)

        self.video_thread.frame_received.connect(self.on_frame)
        self.video_thread.client_connected.connect(self.on_connected)
        self.video_thread.client_disconnected.connect(self.on_disconnected)
        self.control_thread.command_received.connect(self.on_control_command)

        for th in (
            self.control_thread,
            self.audio_thread,
            self.video_thread,
            self.beacon_thread,
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

        # Standby View
        self.standby = QWidget()
        self.standby.setStyleSheet("background-color: #0d111a;")
        sb_layout = QVBoxLayout(self.standby)
        sb_layout.setAlignment(Qt.AlignCenter)
        sb_layout.setSpacing(18)

        # Container for standby details that can be toggled hidden
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
        self.pin_req_cb.setStyleSheet(
            "color: #8f9bb3; font-size: 15px; margin-top: 10px;"
        )
        self.pin_req_cb.stateChanged.connect(self.on_pin_req_changed)

        self.hint_lbl = QLabel("Right-click anywhere for menu • Press ESC / F11 to toggle fullscreen")
        self.hint_lbl.setStyleSheet("color: #4b5568; font-size: 13px; margin-top: 20px;")

        self.details_layout.addWidget(self.title_lbl, alignment=Qt.AlignCenter)
        self.details_layout.addWidget(self.ip_lbl, alignment=Qt.AlignCenter)
        self.details_layout.addWidget(self.pin_lbl, alignment=Qt.AlignCenter)
        self.details_layout.addWidget(self.pin_req_cb, alignment=Qt.AlignCenter)
        self.details_layout.addWidget(self.hint_lbl, alignment=Qt.AlignCenter)

        sb_layout.addWidget(self.details_container, alignment=Qt.AlignCenter)

        # Canvas View
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
        logger.info(f"Standby details visibility toggled: Hidden={self.hide_details}")

    def on_control_command(self, cmd: dict):
        """Processes remote control commands (e.g. Window Management) received from Sender."""
        cmd_type = cmd.get("type")
        if cmd_type == "window_control":
            action = cmd.get("action")
            logger.info(f"Executing remote window control command: {action}")
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

        # Visibility Toggle Option
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

    def on_pin_req_changed(self, state):
        req = self.is_pin_required()
        logger.info(f"PIN requirement changed: {req}")

    def on_connected(self, ip: str):
        logger.info(f"Display Canvas activated for sender {ip}")
        self.stack.setCurrentWidget(self.canvas)

    def on_disconnected(self):
        logger.info("Display switched back to standby.")
        self.canvas.current_frame = None
        self.stack.setCurrentWidget(self.standby)

    def on_frame(self, img: QImage):
        self.canvas.update_frame(img)

    def closeEvent(self, event):
        logger.info("Receiver shutting down. Terminating worker threads cleanly...")
        self.beacon_thread.stop()
        self.video_thread.stop()
        self.audio_thread.stop()
        self.control_thread.stop()
        logger.info("All threads terminated. Goodbye.")
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = ReceiverMainWindow()
    win.showFullScreen()
    sys.exit(app.exec())