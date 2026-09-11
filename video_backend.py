"""
Hardware Mouse Cursor Coordinate Extractor & Anti-Aliased Overlay Renderer.
"""

import ctypes
from ctypes import Structure, byref, c_long
import sys

import cv2
import numpy as np
from PySide6.QtGui import QCursor


class POINT(Structure):
    _fields_ = [("x", c_long), ("y", c_long)]


def get_system_cursor_position() -> tuple[int, int]:
    """Retrieves absolute global mouse cursor screen coordinates."""
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
    """Draws a high-contrast anti-aliased mouse pointer onto the frame."""
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
        cv2.polylines(
            bgr_image, [pts], isClosed=True, color=(0, 0, 0), thickness=2, lineType=cv2.LINE_AA
        )
        cv2.fillPoly(bgr_image, [pts], color=(255, 255, 255), lineType=cv2.LINE_AA)
        cv2.polylines(
            bgr_image, [pts], isClosed=True, color=(20, 20, 20), thickness=1, lineType=cv2.LINE_AA
        )