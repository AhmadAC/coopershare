"""
MrCoopersScreenShare - Receiver (Interactive Touch Display & Sound Hub)
Features: UDP Broadcast Beacon, 4-Digit PIN Authentication, Multi-Touch Canvas.
"""

import json
import random
import socket
import struct
import sys
import time
from typing import Optional

import cv2
import numpy as np
from PySide6.QtCore import QEvent, QPointF, Qt, QThread, Signal
from PySide6.QtGui import QFont, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

try:
    import sounddevice as sd

    AUDIO_AVAILABLE = True
except Exception:
    AUDIO_AVAILABLE = False

VIDEO_PORT = 9988
CONTROL_PORT = 9989
AUDIO_PORT = 9990
DISCOVERY_PORT = 9991
SAMPLE_RATE = 44100
CHANNELS = 2


def get_local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


# ---------------------------------------------------------------------------
# Server & Beacon Threads
# ---------------------------------------------------------------------------


class DiscoveryBeaconThread(QThread):
    """Periodically broadcasts receiver IP & PIN requirement across the LAN."""

    def __init__(self, get_pin_func, get_pin_req_func):
        super().__init__()
        self.get_pin_func = get_pin_func
        self.get_pin_req_func = get_pin_req_func
        self.running = True

    def run(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

        while self.running:
            try:
                local_ip = get_local_ip()
                payload = json.dumps(
                    {
                        "service": "MrCoopersScreenShare",
                        "ip": local_ip,
                        "pin_required": self.get_pin_req_func(),
                    }
                ).encode("utf-8")
                sock.sendto(payload, ("255.255.255.255", DISCOVERY_PORT))
            except Exception:
                pass
            self.msleep(1500)
        sock.close()

    def stop(self):
        self.running = False
        self.wait()


class VideoServerThread(QThread):
    frame_received = Signal(QImage)
    client_connected = Signal(str)
    client_disconnected = Signal()

    def __init__(
        self, get_pin_func, get_pin_req_func, port: int = VIDEO_PORT
    ):
        super().__init__()
        self.port = port
        self.get_pin_func = get_pin_func
        self.get_pin_req_func = get_pin_req_func
        self.running = True
        self.server_sock: Optional[socket.socket] = None

    def run(self):
        self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_sock.bind(("0.0.0.0", self.port))
        self.server_sock.listen(1)

        while self.running:
            try:
                conn, addr = self.server_sock.accept()
                if self._verify_handshake(conn):
                    self.client_connected.emit(addr[0])
                    self._handle_client(conn)
                else:
                    conn.close()
            except Exception:
                break

    def _verify_handshake(self, conn: socket.socket) -> bool:
        """Verifies 4-digit PIN if PIN requirement is active."""
        try:
            conn.settimeout(5.0)
            header = conn.recv(4)
            if not header:
                return False
            size = struct.unpack(">L", header)[0]
            data = json.loads(conn.recv(size).decode("utf-8"))

            pin_req = self.get_pin_req_func()
            client_pin = data.get("pin", "")

            if pin_req and client_pin != self.get_pin_func():
                resp = json.dumps(
                    {"auth": False, "msg": "Incorrect PIN"}
                ).encode("utf-8")
                conn.sendall(struct.pack(">L", len(resp)) + resp)
                return False

            resp = json.dumps({"auth": True}).encode("utf-8")
            conn.sendall(struct.pack(">L", len(resp)) + resp)
            conn.settimeout(None)
            return True
        except Exception:
            return False

    def _handle_client(self, conn: socket.socket):
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        payload_size = struct.calcsize(">L")
        data = bytearray()

        while self.running:
            try:
                while len(data) < payload_size:
                    packet = conn.recv(4096)
                    if not packet:
                        raise ConnectionResetError
                    data.extend(packet)

                msg_size = struct.unpack(">L", data[:payload_size])[0]
                data = data[payload_size:]

                while len(data) < msg_size:
                    packet = conn.recv(min(msg_size - len(data), 65536))
                    if not packet:
                        raise ConnectionResetError
                    data.extend(packet)

                frame_data = data[:msg_size]
                data = data[msg_size:]

                np_arr = np.frombuffer(frame_data, np.uint8)
                img = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
                if img is not None:
                    img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                    h, w, ch = img_rgb.shape
                    qimg = QImage(
                        img_rgb.data, w, h, ch * w, QImage.Format_RGB888
                    ).copy()
                    self.frame_received.emit(qimg)
            except Exception:
                break

        conn.close()
        self.client_disconnected.emit()

    def stop(self):
        self.running = False
        if self.server_sock:
            self.server_sock.close()
        self.wait()


class AudioServerThread(QThread):
    def __init__(self, port: int = AUDIO_PORT):
        super().__init__()
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

        try:
            out_stream = sd.OutputStream(
                samplerate=SAMPLE_RATE, channels=CHANNELS, dtype="int16"
            )
            out_stream.start()
        except Exception:
            return

        while self.running:
            try:
                conn, _ = self.server_sock.accept()
                while self.running:
                    pcm_data = conn.recv(4096)
                    if not pcm_data:
                        break
                    samples = np.frombuffer(pcm_data, dtype=np.int16)
                    out_stream.write(samples)
                conn.close()
            except Exception:
                break

        out_stream.stop()
        out_stream.close()

    def stop(self):
        self.running = False
        if self.server_sock:
            self.server_sock.close()
        self.wait()


class ControlServerThread(QThread):
    def __init__(self, port: int = CONTROL_PORT):
        super().__init__()
        self.port = port
        self.running = True
        self.client_conn: Optional[socket.socket] = None
        self.server_sock: Optional[socket.socket] = None

    def run(self):
        self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_sock.bind(("0.0.0.0", self.port))
        self.server_sock.listen(1)

        while self.running:
            try:
                conn, _ = self.server_sock.accept()
                conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                self.client_conn = conn
            except Exception:
                break

    def send_event(self, event_data: dict):
        if self.client_conn:
            try:
                msg = json.dumps(event_data).encode("utf-8")
                self.client_conn.sendall(struct.pack(">L", len(msg)) + msg)
            except Exception:
                self.client_conn = None

    def stop(self):
        self.running = False
        if self.client_conn:
            self.client_conn.close()
        if self.server_sock:
            self.server_sock.close()
        self.wait()


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

    def _get_video_rect(self):
        if not self.current_frame:
            return self.rect()
        pix_size = self.current_frame.size()
        pix_size.scale(self.size(), Qt.KeepAspectRatio)
        return (
            (self.width() - pix_size.width()) // 2,
            (self.height() - pix_size.height()) // 2,
            pix_size.width(),
            pix_size.height(),
        )

    def _normalize_pos(self, pos: QPointF) -> Optional[tuple[float, float]]:
        vx, vy, vw, vh = self._get_video_rect()
        if vw == 0 or vh == 0:
            return None
        nx, ny = (pos.x() - vx) / vw, (pos.y() - vy) / vh
        return (nx, ny) if 0.0 <= nx <= 1.0 and 0.0 <= ny <= 1.0 else None

    def paintEvent(self, event):
        painter = QPainter(self)
        if self.current_frame:
            vx, vy, vw, vh = self._get_video_rect()
            scaled = self.current_frame.scaled(
                vw, vh, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            painter.drawPixmap(vx, vy, scaled)

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
        norm = self._normalize_pos(event.position())
        if norm:
            self.control_server.send_event(
                {
                    "type": "mouse_down",
                    "x": norm[0],
                    "y": norm[1],
                    "button": (
                        "left"
                        if event.button() == Qt.LeftButton
                        else "right"
                    ),
                }
            )

    def mouseMoveEvent(self, event):
        norm = self._normalize_pos(event.position())
        if norm:
            self.control_server.send_event(
                {"type": "mouse_move", "x": norm[0], "y": norm[1]}
            )

    def mouseReleaseEvent(self, event):
        norm = self._normalize_pos(event.position())
        if norm:
            self.control_server.send_event(
                {
                    "type": "mouse_up",
                    "x": norm[0],
                    "y": norm[1],
                    "button": (
                        "left"
                        if event.button() == Qt.LeftButton
                        else "right"
                    ),
                }
            )

    def wheelEvent(self, event):
        self.control_server.send_event(
            {"type": "scroll", "dy": event.angleDelta().y()}
        )


class ReceiverMainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("MrCoopersScreenShare - Receiver")
        self.resize(1280, 800)

        # Generate 4-digit PIN for session
        self.pin = str(random.randint(1000, 9999))

        self.control_thread = ControlServerThread()
        self.audio_thread = AudioServerThread()
        self.video_thread = VideoServerThread(
            self.get_pin, self.is_pin_required
        )
        self.beacon_thread = DiscoveryBeaconThread(
            self.get_pin, self.is_pin_required
        )

        self.video_thread.frame_received.connect(self.on_frame)
        self.video_thread.client_connected.connect(self.on_connected)
        self.video_thread.client_disconnected.connect(self.on_disconnected)

        for th in (
            self.control_thread,
            self.audio_thread,
            self.video_thread,
            self.beacon_thread,
        ):
            th.start()

        self._setup_ui()

    def get_pin(self) -> str:
        return self.pin

    def is_pin_required(self) -> bool:
        return self.pin_req_cb.isChecked()

    def _setup_ui(self):
        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)

        # Standby View
        self.standby = QWidget()
        self.standby.setStyleSheet("background-color: #12161f;")
        sb_layout = QVBoxLayout(self.standby)
        sb_layout.setAlignment(Qt.AlignCenter)
        sb_layout.setSpacing(14)

        title = QLabel("MrCoopersScreenShare")
        title.setFont(QFont("Segoe UI", 34, QFont.Bold))
        title.setStyleSheet("color: #00a2ed;")

        ip_lbl = QLabel(f"Display IP: {get_local_ip()}")
        ip_lbl.setFont(QFont("Segoe UI", 22))
        ip_lbl.setStyleSheet("color: #ffffff;")

        pin_lbl = QLabel(f"PIN: {self.pin}")
        pin_lbl.setFont(QFont("Segoe UI", 28, QFont.Bold))
        pin_lbl.setStyleSheet("color: #00d084; letter-spacing: 4px;")

        self.pin_req_cb = QCheckBox("Require 4-digit PIN to Connect")
        self.pin_req_cb.setChecked(False)  # Unchecked = Instant Auto-Connect
        self.pin_req_cb.setStyleSheet(
            "color: #8f9bb3; font-size: 14px; margin-top: 10px;"
        )

        sb_layout.addWidget(title, alignment=Qt.AlignCenter)
        sb_layout.addWidget(ip_lbl, alignment=Qt.AlignCenter)
        sb_layout.addWidget(pin_lbl, alignment=Qt.AlignCenter)
        sb_layout.addWidget(self.pin_req_cb, alignment=Qt.AlignCenter)

        # Canvas View
        self.canvas = TouchDisplayCanvas(self.control_thread)
        self.stack.addWidget(self.standby)
        self.stack.addWidget(self.canvas)

    def on_connected(self, ip: str):
        self.stack.setCurrentWidget(self.canvas)

    def on_disconnected(self):
        self.canvas.current_frame = None
        self.stack.setCurrentWidget(self.standby)

    def on_frame(self, img: QImage):
        self.canvas.update_frame(img)

    def closeEvent(self, event):
        self.video_thread.stop()
        self.audio_thread.stop()
        self.control_thread.stop()
        self.beacon_thread.stop()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = ReceiverMainWindow()
    win.show()
    sys.exit(app.exec())