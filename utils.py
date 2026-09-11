"""
Persistence, image generation, and low-level socket utilities.
"""

import json
import os
import socket
import sys
from typing import Optional

import mss
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap


def get_app_directory() -> str:
    """Returns the base directory where sender is running."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


HISTORY_FILE_PATH = os.path.join(get_app_directory(), "history.json")


def load_history() -> dict:
    """Loads saved devices and last connected IP from history.json."""
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