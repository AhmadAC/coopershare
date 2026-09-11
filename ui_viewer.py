"""
Interactive Remote Receiver Viewer Canvas & Control Window.
"""

from typing import Optional

from PySide6.QtCore import QPointF, QRect, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QKeyEvent, QMouseEvent, QPainter, QPixmap
from PySide6.QtWidgets import QMainWindow, QWidget

from threads import ReverseScreenReceiverThread


class RemoteReceiverCanvas(QWidget):
    """Interactive canvas capturing mouse & keyboard events to control receiver desktop."""

    def __init__(self, send_command_func, parent=None):
        super().__init__(parent)
        self.send_command_func = send_command_func
        self.current_frame: Optional[QPixmap] = None
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setStyleSheet("background-color: #0d111a;")

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
        if r.width() <= 0 or r.height() <= 0:
            return None
        nx = (pos.x() - r.x()) / r.width()
        ny = (pos.y() - r.y()) / r.height()
        nx = max(0.0, min(1.0, nx))
        ny = max(0.0, min(1.0, ny))
        return (nx, ny)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

        if self.current_frame and not self.current_frame.isNull():
            target_rect = self._get_video_rect()
            painter.drawPixmap(target_rect, self.current_frame)
        else:
            painter.setPen(QColor("#8f9bb3"))
            painter.setFont(QFont("Segoe UI", 14))
            painter.drawText(self.rect(), Qt.AlignCenter, "Connecting to TV Screen Stream...")

    def mousePressEvent(self, event: QMouseEvent):
        self.setFocus()
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
        self.send_command_func(
            {"type": "remote_input", "event": {"type": "scroll", "dy": event.angleDelta().y()}}
        )

    def keyPressEvent(self, event: QKeyEvent):
        key_name = (
            event.text()
            if event.text()
            and event.key()
            not in (
                Qt.Key_Return,
                Qt.Key_Enter,
                Qt.Key_Backspace,
                Qt.Key_Tab,
                Qt.Key_Escape,
            )
            else event.keyCombination().key().name
        )

        self.send_command_func(
            {
                "type": "remote_input",
                "event": {
                    "type": "key_down",
                    "key": key_name,
                    "text": event.text(),
                },
            }
        )
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent):
        key_name = (
            event.text()
            if event.text()
            and event.key()
            not in (
                Qt.Key_Return,
                Qt.Key_Enter,
                Qt.Key_Backspace,
                Qt.Key_Tab,
                Qt.Key_Escape,
            )
            else event.keyCombination().key().name
        )

        self.send_command_func(
            {
                "type": "remote_input",
                "event": {
                    "type": "key_up",
                    "key": key_name,
                    "text": event.text(),
                },
            }
        )
        super().keyReleaseEvent(event)


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

    def on_stream_disconnected(self):
        self.setWindowTitle(f"Receiver Desktop Viewer ({self.target_ip}) - Disconnected")

    def closeEvent(self, event):
        if self.stream_thread:
            self.stream_thread.stop()
        self.viewer_closed.emit()
        event.accept()