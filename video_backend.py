"""
Hardware Mouse Cursor Coordinate Extractor & Anti-Aliased Overlay Renderer.
Supports Windows Win32 API, Wayland / X11 Device Pixel Ratio (DPR) fractional scaling.
"""

import ctypes
from ctypes import Structure, byref, c_long
import sys

import cv2
import numpy as np
from PySide6.QtGui import QCursor, QGuiApplication


class POINT(Structure):
    _fields_ = [("x", c_long), ("y", c_long)]


def get_system_cursor_position() -> tuple[float, float]:
    """Retrieves absolute global mouse cursor screen coordinates."""
    if sys.platform == "win32":
        try:
            pt = POINT()
            ctypes.windll.user32.GetCursorPos(byref(pt))
            return float(pt.x), float(pt.y)
        except Exception:
            pass
    pos = QCursor.pos()
    return float(pos.x()), float(pos.y())


def render_cursor_on_frame(
    bgr_image: np.ndarray,
    monitor_left: int = 0,
    monitor_top: int = 0,
    scale_factor: float = 1.0,
):
    """Draws a high-contrast anti-aliased mouse pointer onto the frame with DPR scaling."""
    gx, gy = get_system_cursor_position()

    # Apply Device Pixel Ratio scaling for Wayland / High-DPI screens
    if scale_factor <= 0.0 or scale_factor == 1.0:
        screen = QGuiApplication.primaryScreen()
        if screen:
            scale_factor = float(screen.devicePixelRatio())
        else:
            scale_factor = 1.0

    cx = int(round((gx - monitor_left) * scale_factor))
    cy = int(round((gy - monitor_top) * scale_factor))

    h, w = bgr_image.shape[:2]
    if 0 <= cx < w and 0 <= cy < h:
        # Scale the cursor polygon proportionally with resolution / DPI
        size_mult = max(1.0, scale_factor)
        base_pts = np.array(
            [
                [0, 0],
                [0, 19],
                [5, 15],
                [9, 23],
                [12, 22],
                [8, 14],
                [15, 14],
            ],
            dtype=np.float32,
        )
        scaled_pts = (base_pts * size_mult).astype(np.int32)
        pts = scaled_pts + np.array([cx, cy], dtype=np.int32)

        # Draw outer black outline for high contrast
        cv2.polylines(
            bgr_image,
            [pts],
            isClosed=True,
            color=(0, 0, 0, 255) if bgr_image.shape[2] == 4 else (0, 0, 0),
            thickness=max(2, int(round(2 * size_mult))),
            lineType=cv2.LINE_AA,
        )
        # Draw solid white interior
        cv2.fillPoly(
            bgr_image,
            [pts],
            color=(255, 255, 255, 255) if bgr_image.shape[2] == 4 else (255, 255, 255),
            lineType=cv2.LINE_AA,
        )
        # Draw inner dark edge
        cv2.polylines(
            bgr_image,
            [pts],
            isClosed=True,
            color=(20, 20, 20, 255) if bgr_image.shape[2] == 4 else (20, 20, 20),
            thickness=1,
            lineType=cv2.LINE_AA,
        )