#################### START OF FILE: ui_viewer.py ####################

# ui_viewer.py

"""
Interactive Remote Receiver Viewer Canvas & Control Window.
Captures and forwards left click, right click, middle click, mouse move, scroll,
and keystrokes to the target receiver display.
Uses zero-copy direct QImage surface painting to achieve fluid 60 FPS remote screen viewing.
Includes native capture exclusion on Windows.
"""

import sys
from typing import Optional

from PySide6.QtCore import QPointF, QRect, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QKeyEvent, QMouseEvent, QPainter
from PySide6.QtWidgets import QMainWindow, QWidget

from threads import ReverseScreenReceiverThread
from utils import exclude_from_capture


class RemoteReceiverCanvas(QWidget):
    """Interactive canvas capturing mouse & keyboard events to control receiver desktop."""

    def __init__(self, send_command_func, parent=None):
        super().__init__(parent)
        self.send_command_func = send_command_func
        self.current_frame: Optional[QImage] = None
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setStyleSheet("background-color: #0d111a;")

    def update_frame(self, qimage: QImage):
        self.current_frame = qimage
        self.update()

    def _get_video_rect(self) -> QRect:
        if not self.current_frame or self.current_frame.isNull():
            return self.rect()
        img_size = self.current_frame.size()
        img_size.scale(self.size(), Qt.KeepAspectRatio)
        return QRect(
            (self.width() - img_size.width()) // 2,
            (self.height() - img_size.height()) // 2,
            img_size.width(),
            img_size.height(),
        )

    def _normalize_pos(self, pos: QPointF) -> Optional[tuple[float, float]]:
        r = self._get_video_rect()
        if r.width() <= 0 or r.height() <= 0:
            return None
        nx = (pos.x() - r.x()) / r.width()
        ny = (pos.y() - r.y()) / r.height()
        nx = max(0.0, min(1.0, nx))
        ny = max(0.0, min(1.0, ny))
        return (nx, ny)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#0d111a"))

        if self.current_frame and not self.current_frame.isNull():
            painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
            target_rect = self._get_video_rect()
            painter.drawImage(target_rect, self.current_frame)
        else:
            painter.setPen(QColor("#8f9bb3"))
            painter.setFont(QFont("Segoe UI", 14))
            painter.drawText(self.rect(), Qt.AlignCenter, "Connecting to TV Screen Stream...")

    def enterEvent(self, event):
        self.setFocus(Qt.MouseFocusReason)
        super().enterEvent(event)

    def mousePressEvent(self, event: QMouseEvent):
        self.setFocus(Qt.MouseFocusReason)
        norm = self._normalize_pos(event.position())
        if norm:
            btn = (
                "right"
                if event.button() == Qt.RightButton
                else ("middle" if event.button() == Qt.MiddleButton else "left")
            )
            self.send_command_func(
                {
                    "type": "remote_input",
                    "event": {
                        "type": "mouse_down",
                        "x": norm[0],
                        "y": norm[1],
                        "button": btn,
                    },
                }
            )

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        self.mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        norm = self._normalize_pos(event.position())
        if norm:
            self.send_command_func(
                {
                    "type": "remote_input",
                    "event": {"type": "mouse_move", "x": norm[0], "y": norm[1]},
                }
            )

    def mouseReleaseEvent(self, event: QMouseEvent):
        norm = self._normalize_pos(event.position())
        if norm:
            btn = (
                "right"
                if event.button() == Qt.RightButton
                else ("middle" if event.button() == Qt.MiddleButton else "left")
            )
            self.send_command_func(
                {
                    "type": "remote_input",
                    "event": {
                        "type": "mouse_up",
                        "x": norm[0],
                        "y": norm[1],
                        "button": btn,
                    },
                }
            )

    def wheelEvent(self, event):
        dy = event.angleDelta().y()
        self.send_command_func(
            {"type": "remote_input", "event": {"type": "scroll", "dy": dy}}
        )

    def keyPressEvent(self, event: QKeyEvent):
        nvk = event.nativeVirtualKey()
        key_code = nvk if nvk else event.key()
        key_name = event.keyCombination().key().name.replace("Key_", "")
        text = event.text()

        self.send_command_func(
            {
                "type": "remote_input",
                "event": {
                    "type": "key_down",
                    "key_code": key_code,
                    "key": key_name,
                    "text": text,
                },
            }
        )
        event.accept()

    def keyReleaseEvent(self, event: QKeyEvent):
        nvk = event.nativeVirtualKey()
        key_code = nvk if nvk else event.key()
        key_name = event.keyCombination().key().name.replace("Key_", "")
        text = event.text()

        self.send_command_func(
            {
                "type": "remote_input",
                "event": {
                    "type": "key_up",
                    "key_code": key_code,
                    "key": key_name,
                    "text": text,
                },
            }
        )
        event.accept()

    def contextMenuEvent(self, event):
        event.accept()


class RemoteReceiverViewerWindow(QMainWindow):
    """Viewer window displaying the live stream with interactive mouse and keyboard control."""

    viewer_closed = Signal()

    def __init__(self, target_ip: str, send_command_func, parent=None):
        super().__init__(parent)
        self.target_ip = target_ip
        self.send_command_func = send_command_func
        self.setWindowTitle(f"Receiver Desktop Viewer & Controller ({target_ip})")
        self.resize(1280, 720)
        self.setStyleSheet("background-color: #0b0e14;")

        self.canvas = RemoteReceiverCanvas(self.send_command_func, self)
        self.setCentralWidget(self.canvas)

        self.stream_thread = ReverseScreenReceiverThread(target_ip)
        self.stream_thread.frame_received.connect(self.canvas.update_frame)
        self.stream_thread.disconnected.connect(self.on_stream_disconnected)
        self.stream_thread.start()

        if sys.platform == "win32":
            exclude_from_capture(self)

    def showEvent(self, event):
        if sys.platform == "win32":
            exclude_from_capture(self)
        super().showEvent(event)

    def on_stream_disconnected(self):
        self.setWindowTitle(f"Receiver Desktop Viewer ({self.target_ip}) - Disconnected")

    def closeEvent(self, event):
        if self.stream_thread:
            self.stream_thread.stop()
        self.viewer_closed.emit()
        event.accept()