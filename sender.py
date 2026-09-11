# sender.py
"""
MrCoopersScreenShare - Sender (PC Presenter & Remote Controller)
Features: Persistent Always-On-Top Collapsed Mini Pill (WS_EX_TOPMOST + Non-Intrusive Guard),
          Interactive Remote Receiver Screen Viewer & Controller (Independent Control Channel),
          Full Mouse & Keyboard Input Redirection (Clicks, Moves, Drags, Typing & Hotkeys),
          Anti-Loop Screen Isolation on Remote Viewing,
          Cursorless Freeze-Frame on Pause, Live Hardware Mouse Overlay on Stream,
          Remote Timer Dispatch Dialog (Sends raw sys.argv to Receiver's linked timer),
          Remote Receiver Window Management,
          Device Name & IP Manager (Friendly Name Selection from Dropdown & Right-Click Context Menu),
          Receiver Touch / Input Injection Toggle, Host Speaker Mute Toggle,
          TV Audio Volume Slider (0% - 150%) with True Zero Silence Output on Mute,
          Resume Button (Orange) & TV Muted Button (Red) styling,
          Collapsed Mode Taskbar Click Non-Minimize Red Pulsing Animation (3 Seconds),
          Live Mid-Stream FPS & Quality Switching, Ctrl+Click Mini Pill Pause/Resume Toggle,
          Native 64-bit Windows WASAPI Desktop Audio Loopback (Driverless COM ctypes),
          4:4:4 Chroma Subsampling Ultra Crisp Text.
"""

import ctypes
from ctypes import (
    HRESULT,
    POINTER,
    Structure,
    byref,
    c_float,
    c_int,
    c_int64,
    c_long,
    c_short,
    c_ubyte,
    c_uint,
    c_uint64,
    c_ulong,
    c_ushort,
    c_void_p,
)
import json
import math
import os
import runpy
import socket
import struct
import sys
import threading
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
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt, QThread, QTimer, Signal
from PySide6.QtGui import (
    QAction,
    QColor,
    QCursor,
    QFont,
    QIcon,
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
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QPushButton,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

# Optional Sound Support Fallback
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
REVERSE_VIDEO_PORT = 9992
DEFAULT_SAMPLE_RATE = 48000
CHANNELS = 2
SOCKET_BUFFER_SIZE = 2 * 1024 * 1024  # 2MB High-Throughput Buffer

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


# ---------------------------------------------------------------------------
# Mouse Cursor Overlay Renderer
# ---------------------------------------------------------------------------


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
        cv2.polylines(bgr_image, [pts], isClosed=True, color=(0, 0, 0), thickness=2, lineType=cv2.LINE_AA)
        cv2.fillPoly(bgr_image, [pts], color=(255, 255, 255), lineType=cv2.LINE_AA)
        cv2.polylines(bgr_image, [pts], isClosed=True, color=(20, 20, 20), thickness=1, lineType=cv2.LINE_AA)


# ---------------------------------------------------------------------------
# Native Windows WASAPI Audio Loopback & Host Speaker Mute (ctypes COM)
# ---------------------------------------------------------------------------


class GUID(Structure):
    _fields_ = [
        ("Data1", c_ulong),
        ("Data2", c_ushort),
        ("Data3", c_ushort),
        ("Data4", c_ubyte * 8),
    ]

    def __init__(self, l, w1, w2, b1, b2, b3, b4, b5, b6, b7, b8):
        super().__init__(l, w1, w2, (c_ubyte * 8)(b1, b2, b3, b4, b5, b6, b7, b8))


class WAVEFORMATEX(Structure):
    _fields_ = [
        ("wFormatTag", c_ushort),
        ("nChannels", c_ushort),
        ("nSamplesPerSec", c_ulong),
        ("nAvgBytesPerSec", c_ulong),
        ("nBlockAlign", c_ushort),
        ("wBitsPerSample", c_ushort),
        ("cbSize", c_ushort),
    ]


CLSID_MMDeviceEnumerator = GUID(
    0xBCDE0395, 0xE52F, 0x467C, 0x8E, 0x3D, 0xC4, 0x57, 0x92, 0x91, 0x69, 0x2E
)
IID_IMMDeviceEnumerator = GUID(
    0xA95664D2, 0x9614, 0x4F35, 0xA7, 0x46, 0xDE, 0x8D, 0xB6, 0x36, 0x17, 0xE6
)
IID_IAudioClient = GUID(
    0x1CB9AD4C, 0xDBFA, 0x4C32, 0xB1, 0x78, 0xC2, 0xF5, 0x68, 0xA7, 0x03, 0xB2
)
IID_IAudioCaptureClient = GUID(
    0xC8ADBD64, 0xE71E, 0x48A0, 0xA4, 0xDE, 0x18, 0x5C, 0x39, 0x5C, 0xD3, 0x17
)
IID_IAudioEndpointVolume = GUID(
    0x5CDF2C82, 0x841E, 0x4546, 0x97, 0x22, 0x0C, 0xF7, 0x40, 0x78, 0x22, 0x9A
)

AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000
AUDCLNT_SHAREMODE_SHARED = 0
CLSCTX_ALL = 23


def _release_com_ptr(ptr: c_void_p):
    """Releases an IUnknown COM pointer."""
    if ptr and ptr.value:
        try:
            vtbl = ctypes.cast(ptr, POINTER(POINTER(c_void_p))).contents
            release_func = ctypes.WINFUNCTYPE(c_ulong, c_void_p)(vtbl[2])
            release_func(ptr)
        except Exception:
            pass


class HostAudioController:
    """Controls physical host speaker mute without affecting loopback capture."""

    @staticmethod
    def set_host_mute(mute: bool) -> bool:
        if sys.platform != "win32":
            return False

        ole32 = ctypes.windll.ole32
        ole32.CoInitialize(None)
        p_enum = c_void_p()
        p_dev = c_void_p()
        p_ep_vol = c_void_p()

        try:
            hr = ole32.CoCreateInstance(
                byref(CLSID_MMDeviceEnumerator),
                None,
                CLSCTX_ALL,
                byref(IID_IMMDeviceEnumerator),
                byref(p_enum),
            )
            if hr != 0 or not p_enum.value:
                return False

            enum_vtbl = ctypes.cast(p_enum, POINTER(POINTER(c_void_p))).contents
            get_endpoint = ctypes.WINFUNCTYPE(
                HRESULT, c_void_p, c_int, c_int, POINTER(c_void_p)
            )(enum_vtbl[4])
            hr = get_endpoint(p_enum, 0, 0, byref(p_dev))
            if hr != 0 or not p_dev.value:
                return False

            dev_vtbl = ctypes.cast(p_dev, POINTER(POINTER(c_void_p))).contents
            activate = ctypes.WINFUNCTYPE(
                HRESULT, c_void_p, POINTER(GUID), c_ulong, c_void_p, POINTER(c_void_p)
            )(dev_vtbl[3])

            hr = activate(p_dev, byref(IID_IAudioEndpointVolume), CLSCTX_ALL, None, byref(p_ep_vol))
            if hr != 0 or not p_ep_vol.value:
                return False

            ep_vtbl = ctypes.cast(p_ep_vol, POINTER(POINTER(c_void_p))).contents
            set_mute = ctypes.WINFUNCTYPE(
                HRESULT, c_void_p, c_int, c_void_p
            )(ep_vtbl[14])
            hr = set_mute(p_ep_vol, 1 if mute else 0, None)
            return hr == 0
        except Exception as ex:
            print(f"[DEBUG Host Audio] Set mute exception: {ex}")
            return False
        finally:
            _release_com_ptr(p_ep_vol)
            _release_com_ptr(p_dev)
            _release_com_ptr(p_enum)

    @staticmethod
    def get_host_mute() -> bool:
        if sys.platform != "win32":
            return False

        ole32 = ctypes.windll.ole32
        ole32.CoInitialize(None)
        p_enum = c_void_p()
        p_dev = c_void_p()
        p_ep_vol = c_void_p()

        try:
            hr = ole32.CoCreateInstance(
                byref(CLSID_MMDeviceEnumerator),
                None,
                CLSCTX_ALL,
                byref(IID_IMMDeviceEnumerator),
                byref(p_enum),
            )
            if hr != 0 or not p_enum.value:
                return False

            enum_vtbl = ctypes.cast(p_enum, POINTER(POINTER(c_void_p))).contents
            get_endpoint = ctypes.WINFUNCTYPE(
                HRESULT, c_void_p, c_int, c_int, POINTER(c_void_p)
            )(enum_vtbl[4])
            hr = get_endpoint(p_enum, 0, 0, byref(p_dev))
            if hr != 0 or not p_dev.value:
                return False

            dev_vtbl = ctypes.cast(p_dev, POINTER(POINTER(c_void_p))).contents
            activate = ctypes.WINFUNCTYPE(
                HRESULT, c_void_p, POINTER(GUID), c_ulong, c_void_p, POINTER(c_void_p)
            )(dev_vtbl[3])

            hr = activate(p_dev, byref(IID_IAudioEndpointVolume), CLSCTX_ALL, None, byref(p_ep_vol))
            if hr != 0 or not p_ep_vol.value:
                return False

            ep_vtbl = ctypes.cast(p_ep_vol, POINTER(POINTER(c_void_p))).contents
            get_mute = ctypes.WINFUNCTYPE(
                HRESULT, c_void_p, POINTER(c_int)
            )(ep_vtbl[15])
            is_muted = c_int(0)
            hr = get_mute(p_ep_vol, byref(is_muted))
            return bool(is_muted.value) if hr == 0 else False
        except Exception:
            return False
        finally:
            _release_com_ptr(p_ep_vol)
            _release_com_ptr(p_dev)
            _release_com_ptr(p_enum)


class NativeWindowsWasapiLoopback:
    """Zero-dependency direct 64-bit Windows WASAPI desktop speaker loopback capture."""

    def __init__(self):
        self.initialized = False
        self.sample_rate = DEFAULT_SAMPLE_RATE
        self.channels = CHANNELS
        self.bits_per_sample = 32
        self.is_float = True
        self.audio_client = None
        self.capture_client = None
        self.p_enumerator = None
        self.p_device = None

    def start(self) -> bool:
        if sys.platform != "win32":
            return False

        try:
            ole32 = ctypes.windll.ole32
            ole32.CoInitialize(None)

            self.p_enumerator = c_void_p()
            hr = ole32.CoCreateInstance(
                byref(CLSID_MMDeviceEnumerator),
                None,
                CLSCTX_ALL,
                byref(IID_IMMDeviceEnumerator),
                byref(self.p_enumerator),
            )
            if hr != 0 or not self.p_enumerator.value:
                return False

            enum_vtbl = ctypes.cast(
                self.p_enumerator, POINTER(POINTER(c_void_p))
            ).contents
            get_endpoint_func = ctypes.WINFUNCTYPE(
                HRESULT, c_void_p, c_int, c_int, POINTER(c_void_p)
            )(enum_vtbl[4])

            self.p_device = c_void_p()
            hr = get_endpoint_func(self.p_enumerator, 0, 0, byref(self.p_device))
            if hr != 0 or not self.p_device.value:
                return False

            dev_vtbl = ctypes.cast(self.p_device, POINTER(POINTER(c_void_p))).contents
            activate_func = ctypes.WINFUNCTYPE(
                HRESULT, c_void_p, POINTER(GUID), c_ulong, c_void_p, POINTER(c_void_p)
            )(dev_vtbl[3])

            self.audio_client = c_void_p()
            hr = activate_func(
                self.p_device, byref(IID_IAudioClient), CLSCTX_ALL, None, byref(self.audio_client)
            )
            if hr != 0 or not self.audio_client.value:
                return False

            client_vtbl = ctypes.cast(
                self.audio_client, POINTER(POINTER(c_void_p))
            ).contents
            get_format_func = ctypes.WINFUNCTYPE(
                HRESULT, c_void_p, POINTER(POINTER(WAVEFORMATEX))
            )(client_vtbl[8])

            pwfx = POINTER(WAVEFORMATEX)()
            hr = get_format_func(self.audio_client, byref(pwfx))
            if hr != 0 or not pwfx:
                return False

            fmt = pwfx.contents
            self.sample_rate = int(fmt.nSamplesPerSec)
            self.channels = int(fmt.nChannels)
            self.bits_per_sample = int(fmt.wBitsPerSample)
            self.is_float = (fmt.wFormatTag == 3) or (
                fmt.wFormatTag == 0xFFFE and self.bits_per_sample == 32
            )

            init_func = ctypes.WINFUNCTYPE(
                HRESULT, c_void_p, c_int, c_ulong, c_int64, c_int64, c_void_p, c_void_p
            )(client_vtbl[3])
            hr = init_func(
                self.audio_client,
                AUDCLNT_SHAREMODE_SHARED,
                AUDCLNT_STREAMFLAGS_LOOPBACK,
                2000000,
                0,
                pwfx,
                None,
            )
            if hr != 0:
                return False

            get_service_func = ctypes.WINFUNCTYPE(
                HRESULT, c_void_p, POINTER(GUID), POINTER(c_void_p)
            )(client_vtbl[14])
            self.capture_client = c_void_p()
            hr = get_service_func(
                self.audio_client, byref(IID_IAudioCaptureClient), byref(self.capture_client)
            )
            if hr != 0 or not self.capture_client.value:
                return False

            start_func = ctypes.WINFUNCTYPE(HRESULT, c_void_p)(client_vtbl[10])
            start_func(self.audio_client)

            self.initialized = True
            print(
                f"[DEBUG Sender Audio] Native WASAPI loopback active. Rate: {self.sample_rate} Hz, "
                f"Channels: {self.channels}, Bits: {self.bits_per_sample}"
            )
            return True
        except Exception as ex:
            print(f"[DEBUG Sender Audio] Native WASAPI loopback init failed: {ex}")
            return False

    def read_pcm16_chunk(self, volume: float = 1.0) -> Optional[bytes]:
        """Captures available PCM frames converted to 16-bit stereo PCM with volume scaling."""
        if not self.initialized or not self.capture_client:
            return None

        try:
            cap_vtbl = ctypes.cast(
                self.capture_client, POINTER(POINTER(c_void_p))
            ).contents
            get_buffer_func = ctypes.WINFUNCTYPE(
                HRESULT,
                c_void_p,
                POINTER(c_void_p),
                POINTER(c_uint),
                POINTER(c_ulong),
                POINTER(c_uint64),
                POINTER(c_uint64),
            )(cap_vtbl[3])
            release_buffer_func = ctypes.WINFUNCTYPE(HRESULT, c_void_p, c_uint)(
                cap_vtbl[4]
            )
            get_next_packet_func = ctypes.WINFUNCTYPE(
                HRESULT, c_void_p, POINTER(c_uint)
            )(cap_vtbl[5])

            pkt_size = c_uint(0)
            get_next_packet_func(self.capture_client, byref(pkt_size))
            if pkt_size.value == 0:
                return None

            p_data = c_void_p()
            num_frames = c_uint(0)
            flags = c_ulong(0)

            hr = get_buffer_func(
                self.capture_client,
                byref(p_data),
                byref(num_frames),
                byref(flags),
                None,
                None,
            )
            if hr != 0 or num_frames.value == 0 or not p_data.value:
                return None

            frame_count = num_frames.value
            total_samples = frame_count * self.channels

            if flags.value & 0x01 or volume <= 0.0:
                pcm_bytes = b"\x00" * (frame_count * CHANNELS * 2)
            else:
                if self.is_float:
                    float_buf = (c_float * total_samples).from_address(p_data.value)
                    arr = np.ctypeslib.as_array(float_buf).reshape(-1, self.channels)
                    if self.channels > 2:
                        arr = arr[:, :2]
                    elif self.channels == 1:
                        arr = np.repeat(arr, 2, axis=1)

                    scaled = arr * volume
                    pcm16 = (np.clip(scaled, -1.0, 1.0) * 32767.0).astype(np.int16)
                    pcm_bytes = pcm16.tobytes()
                elif self.bits_per_sample == 16:
                    short_buf = (c_short * total_samples).from_address(p_data.value)
                    arr = np.ctypeslib.as_array(short_buf).reshape(-1, self.channels)
                    if self.channels > 2:
                        arr = arr[:, :2]
                    elif self.channels == 1:
                        arr = np.repeat(arr, 2, axis=1)

                    if volume != 1.0:
                        scaled = np.clip(arr.astype(np.float32) * volume, -32768.0, 32767.0).astype(np.int16)
                        pcm_bytes = scaled.tobytes()
                    else:
                        pcm_bytes = arr.astype(np.int16).tobytes()
                else:
                    pcm_bytes = b"\x00" * (frame_count * CHANNELS * 2)

            release_buffer_func(self.capture_client, num_frames)
            return pcm_bytes
        except Exception:
            return None

    def stop(self):
        if self.audio_client:
            try:
                client_vtbl = ctypes.cast(
                    self.audio_client, POINTER(POINTER(c_void_p))
                ).contents
                stop_func = ctypes.WINFUNCTYPE(HRESULT, c_void_p)(client_vtbl[11])
                stop_func(self.audio_client)
            except Exception:
                pass
        self.initialized = False


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
# Background Threads (Discovery, Screen, Audio, Input, Reverse Screen)
# ---------------------------------------------------------------------------


class DiscoveryListenerThread(QThread):
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

    def stop(self):
        self.running = False
        self.wait(1000)


class ScreenSenderThread(QThread):
    status_changed = Signal(str, bool)

    def __init__(
        self,
        target_ip: str,
        pin: str = "",
        quality: int = 98,
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
        self.send_cursorless_frame_once = False

    def set_fps_limit(self, fps: int):
        self.fps_limit = max(1, fps)
        print(f"[DEBUG Sender Video] Dynamic framerate adjusted to: {self.fps_limit} FPS")

    def set_quality_params(self, quality: int, use_444: bool):
        self.quality = quality
        self.use_444_chroma = use_444
        print(f"[DEBUG Sender Video] Dynamic quality adjusted to: {self.quality}% (4:4:4={self.use_444_chroma})")

    def trigger_cursorless_frame(self):
        """Disables cursor on the next frame so pause displays a clean screen without frozen pointer."""
        self.send_cursorless_frame_once = True

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

            handshake = json.dumps({"pin": self.pin}).encode("utf-8")
            sock.sendall(struct.pack(">L", len(handshake)) + handshake)

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

        with create_mss_instance() as sct:
            monitor = sct.monitors[1]
            mon_left = monitor["left"]
            mon_top = monitor["top"]

            while self.running:
                if self.paused and not self.send_cursorless_frame_once:
                    self.msleep(60)
                    continue

                t_start = time.perf_counter()

                try:
                    raw_frame = sct.grab(monitor)
                    img = np.array(raw_frame)
                    bgr = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)

                    if not self.paused and not self.send_cursorless_frame_once:
                        render_cursor_on_frame(bgr, mon_left, mon_top)

                    encode_params = [int(cv2.IMWRITE_JPEG_QUALITY), self.quality]
                    if hasattr(cv2, "IMWRITE_JPEG_OPTIMIZE"):
                        encode_params.extend([int(cv2.IMWRITE_JPEG_OPTIMIZE), 1])

                    if self.use_444_chroma:
                        sampling_factor_id = getattr(cv2, "IMWRITE_JPEG_SAMPLING_FACTOR", 10)
                        sampling_444_val = getattr(cv2, "IMWRITE_JPEG_SAMPLING_FACTOR_444", 0x00010001)
                        encode_params.extend([int(sampling_factor_id), int(sampling_444_val)])

                    success, enc_img = cv2.imencode(".jpg", bgr, encode_params)

                    if success:
                        data = enc_img.tobytes()
                        sock.sendall(struct.pack(">L", len(data)) + data)

                    if self.send_cursorless_frame_once:
                        self.send_cursorless_frame_once = False

                except Exception as e:
                    print(f"[DEBUG Sender Video] Frame send failed: {e}")
                    break

                elapsed = time.perf_counter() - t_start
                target_frame_time = 1.0 / max(1, self.fps_limit)
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
    def __init__(self, target_ip: str, volume: float = 1.0):
        super().__init__()
        self.target_ip = target_ip
        self.volume = volume
        self.running = True
        self.muted = False
        self.sock: Optional[socket.socket] = None

    def set_volume(self, vol: float):
        self.volume = max(0.0, min(1.5, vol))

    def run(self):
        wasapi = NativeWindowsWasapiLoopback()
        use_native_wasapi = wasapi.start()
        sample_rate = wasapi.sample_rate if use_native_wasapi else DEFAULT_SAMPLE_RATE

        print(
            f"[DEBUG Sender Audio] Connecting to {self.target_ip}:{AUDIO_PORT} "
            f"(Native WASAPI: {use_native_wasapi}, Rate: {sample_rate} Hz, TV Volume: {int(self.volume * 100)}%)..."
        )
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.sock.settimeout(3.0)
            self.sock.connect((self.target_ip, AUDIO_PORT))

            self.sock.sendall(struct.pack(">I", sample_rate))
            self.sock.settimeout(None)
            print("[DEBUG Sender Audio] Connected & streaming live desktop audio.")
        except Exception as e:
            print(f"[DEBUG Sender Audio] Connection failed: {e}")
            wasapi.stop()
            return

        if use_native_wasapi:
            while self.running:
                eff_vol = 0.0 if self.muted else self.volume
                chunk = wasapi.read_pcm16_chunk(volume=eff_vol)
                if chunk and self.sock:
                    try:
                        self.sock.sendall(chunk)
                    except Exception as ex:
                        print(f"[DEBUG Sender Audio] Transmit error: {ex}")
                        break
                else:
                    self.msleep(4)
            wasapi.stop()
        else:
            if AUDIO_AVAILABLE:
                def callback(indata, frames, time_info, status):
                    if self.running and self.sock:
                        try:
                            if self.muted:
                                silence = b"\x00" * (frames * CHANNELS * 2)
                                self.sock.sendall(silence)
                            else:
                                if self.volume != 1.0:
                                    scaled = np.clip(indata.astype(np.float32) * self.volume, -32768.0, 32767.0).astype(np.int16)
                                    self.sock.sendall(scaled.tobytes())
                                else:
                                    self.sock.sendall(indata.tobytes())
                        except Exception:
                            pass

                try:
                    with sd.InputStream(
                        samplerate=sample_rate,
                        channels=CHANNELS,
                        dtype="int16",
                        callback=callback,
                    ):
                        while self.running:
                            self.msleep(100)
                except Exception as ex:
                    print(f"[DEBUG Sender Audio] Audio capture notice: {ex}")

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
    def __init__(self, target_ip: str, is_input_enabled_func):
        super().__init__()
        self.target_ip = target_ip
        self.is_input_enabled_func = is_input_enabled_func
        self.running = True
        self.sock: Optional[socket.socket] = None
        self._send_lock = threading.Lock()

    def send_command(self, cmd: dict):
        """Transmits remote input / control packets to the receiver display."""
        with self._send_lock:
            if self.sock:
                try:
                    data = json.dumps(cmd).encode("utf-8")
                    self.sock.sendall(struct.pack(">L", len(data)) + data)
                except Exception as e:
                    print(f"[DEBUG Sender Control] Failed to transmit command: {e}")

    def run(self):
        print(f"[DEBUG Sender Control] Connecting to {self.target_ip}:{CONTROL_PORT}...")
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.settimeout(3.0)
            sock.connect((self.target_ip, CONTROL_PORT))
            sock.settimeout(0.5)
            with self._send_lock:
                self.sock = sock
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

                if self.is_input_enabled_func():
                    injector.execute(event)
            except ConnectionResetError:
                break
            except Exception as e:
                if self.running:
                    print(f"[DEBUG Sender Control] Input processing error: {e}")
                break

        injector.close()
        with self._send_lock:
            try:
                sock.close()
            except Exception:
                pass
            self.sock = None
        print("[DEBUG Sender Control] Input receiver thread stopped.")

    def stop(self):
        self.running = False
        with self._send_lock:
            if self.sock:
                try:
                    self.sock.close()
                except Exception:
                    pass
                self.sock = None
        self.wait(1000)


class ReverseScreenReceiverThread(QThread):
    frame_received = Signal(QImage)
    disconnected = Signal()

    def __init__(self, target_ip: str, port: int = REVERSE_VIDEO_PORT):
        super().__init__()
        self.target_ip = target_ip
        self.port = port
        self.running = True

    def run(self):
        print(f"[DEBUG Sender Viewer] Connecting to reverse screen stream at {self.target_ip}:{self.port}...")
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.settimeout(4.0)
            sock.connect((self.target_ip, self.port))
            sock.settimeout(0.5)
            print("[DEBUG Sender Viewer] Connected to Receiver Screen Stream.")
        except Exception as e:
            print(f"[DEBUG Sender Viewer] Connection to receiver stream failed: {e}")
            self.disconnected.emit()
            return

        payload_size = struct.calcsize(">L")
        data = bytearray()

        while self.running:
            try:
                while len(data) < payload_size:
                    if not self.running:
                        break
                    try:
                        packet = sock.recv(131072)
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
                        packet = sock.recv(min(msg_size - len(data), 131072))
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
            except Exception as e:
                if self.running:
                    print(f"[DEBUG Sender Viewer] Frame processing error: {e}")
                break

        try:
            sock.close()
        except Exception:
            pass
        self.disconnected.emit()

    def stop(self):
        self.running = False
        self.wait(1000)


# ---------------------------------------------------------------------------
# Interactive Remote Receiver Viewer & Controller Window
# ---------------------------------------------------------------------------


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
        else:
            painter.setPen(QColor("#8f9bb3"))
            painter.setFont(QFont("Segoe UI", 14))
            painter.drawText(self.rect(), Qt.AlignCenter, "Connecting to TV Screen Stream...")

    def mousePressEvent(self, event):
        self.setFocus()
        norm = self._normalize_pos(event.position())
        if norm:
            btn = "right" if event.button() == Qt.RightButton else (
                "middle" if event.button() == Qt.MiddleButton else "left"
            )
            self.send_command_func(
                {"type": "remote_input", "event": {"type": "mouse_down", "x": norm[0], "y": norm[1], "button": btn}}
            )

    def mouseMoveEvent(self, event):
        norm = self._normalize_pos(event.position())
        if norm:
            self.send_command_func(
                {"type": "remote_input", "event": {"type": "mouse_move", "x": norm[0], "y": norm[1]}}
            )

    def mouseReleaseEvent(self, event):
        norm = self._normalize_pos(event.position())
        if norm:
            btn = "right" if event.button() == Qt.RightButton else (
                "middle" if event.button() == Qt.MiddleButton else "left"
            )
            self.send_command_func(
                {"type": "remote_input", "event": {"type": "mouse_up", "x": norm[0], "y": norm[1], "button": btn}}
            )

    def wheelEvent(self, event):
        self.send_command_func(
            {"type": "remote_input", "event": {"type": "scroll", "dy": event.angleDelta().y()}}
        )

    def keyPressEvent(self, event: QKeyEvent):
        key_name = event.text() if event.text() and event.key() not in (
            Qt.Key_Return, Qt.Key_Enter, Qt.Key_Backspace, Qt.Key_Tab, Qt.Key_Escape
        ) else event.keyCombination().key().name

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
        key_name = event.text() if event.text() and event.key() not in (
            Qt.Key_Return, Qt.Key_Enter, Qt.Key_Backspace, Qt.Key_Tab, Qt.Key_Escape
        ) else event.keyCombination().key().name

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


# ---------------------------------------------------------------------------
# Dialog: TV Timer Dispatch
# ---------------------------------------------------------------------------


class TimerDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Receiver Timer")
        self.setFixedWidth(340)
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
                padding: 6px 10px;
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
            """
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        title_lbl = QLabel("Set Receiver Timer")
        title_lbl.setStyleSheet("font-weight: bold; font-size: 13px; color: #00d084;")
        layout.addWidget(title_lbl)

        desc_lbl = QLabel("Enter duration (e.g. 30, 5m, 10:00, or raw number):")
        layout.addWidget(desc_lbl)

        input_layout = QHBoxLayout()
        self.timer_edit = QLineEdit()
        self.timer_edit.setPlaceholderText("30, 5m...")
        self.timer_edit.returnPressed.connect(self.accept)

        self.timer_btn = QPushButton("Start Timer")
        self.timer_btn.clicked.connect(self.accept)

        input_layout.addWidget(self.timer_edit)
        input_layout.addWidget(self.timer_btn)
        layout.addLayout(input_layout)

    def get_args(self) -> str:
        return self.timer_edit.text().strip().strip('"').strip("'")


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
        self.control_thread: Optional[InputReceiverThread] = None
        self.viewer_window: Optional[RemoteReceiverViewerWindow] = None

        self._drag_start_pos = QPoint()
        self._window_start_pos = QPoint()
        self._is_dragging = False

        self.is_mini_mode = False
        self.is_paused = False
        self.is_stream_muted = False
        self.is_host_muted = False
        self.input_enabled = True
        self.tv_volume = 1.0
        self.discovered_ip = ""
        self.pin_required = False
        self.status_color = "#8f9bb3"
        self.was_streaming_before_viewing = False

        # Pulsing Animation attributes for Collapsed Taskbar Click
        self.is_pulsing = False
        self.pulse_start_time = 0.0

        self.history_data = load_history()

        # Non-intrusive Topmost Enforcer Timer for Collapsed Mini Pill
        self.topmost_timer = QTimer(self)
        self.topmost_timer.setInterval(500)
        self.topmost_timer.timeout.connect(self._on_topmost_timer)
        self.topmost_timer.start()

        # 3-Second Red Pulse Timer for taskbar clicks in mini mode
        self.pulse_timer = QTimer(self)
        self.pulse_timer.setInterval(40)
        self.pulse_timer.timeout.connect(self._on_pulse_step)

        self._init_window()
        self._setup_ui()
        self._populate_device_list()

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
        if sys.platform == "win32":
            try:
                hwnd = int(self.winId())
                GWL_EXSTYLE = -20
                WS_EX_TOPMOST = 0x00000008
                ex_style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
                if not (ex_style & WS_EX_TOPMOST):
                    ctypes.windll.user32.SetWindowLongW(
                        hwnd, GWL_EXSTYLE, ex_style | WS_EX_TOPMOST
                    )

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

    def _on_topmost_timer(self):
        if self.is_mini_mode and not self.isMinimized():
            self.enforce_always_on_top()

    def trigger_mini_pulse(self):
        self.is_pulsing = True
        self.pulse_start_time = time.time()
        self._update_mini_bar_style(override_color="#ff3333")
        self.pulse_timer.start()

    def _on_pulse_step(self):
        elapsed = time.time() - self.pulse_start_time
        if elapsed >= 3.0 or not self.is_mini_mode:
            self.pulse_timer.stop()
            self.is_pulsing = False
            self.setWindowOpacity(0.35)
            self._update_mini_bar_style()
            return

        osc = (math.sin(elapsed * math.pi * 3.5) + 1.0) / 2.0
        current_op = 0.30 + (0.60 * osc)
        self.setWindowOpacity(current_op)
        self.enforce_always_on_top()

    def changeEvent(self, event: QEvent):
        if event.type() in (QEvent.WindowStateChange, QEvent.ActivationChange):
            if self.is_mini_mode:
                if self.isMinimized():
                    self.showNormal()
                self.enforce_always_on_top()
                self.trigger_mini_pulse()
            else:
                if not self.isMinimized():
                    self.enforce_always_on_top()
                    self.setWindowOpacity(self.opacity_slider.value() / 100.0)
        super().changeEvent(event)

    def _setup_ui(self):
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        self.stack = QStackedWidget(self)

        # -------------------------------------------------------------------
        # View 1: Expanded Controller Card
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
                border-radius: 6px; font-weight: bold; font-size: 11px; padding: 6px 10px;
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

        # Header Bar
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

        # Row 1: Target Device Dropdown / IP Selector + Share Button
        ip_row = QHBoxLayout()
        self.device_combo = QComboBox()
        self.device_combo.setEditable(True)
        self.device_combo.setPlaceholderText("Select TV or Enter IP...")
        self.device_combo.setToolTip("Select a TV by name or enter an IP. Right-click to assign friendly names.")
        self.device_combo.lineEdit().returnPressed.connect(self.toggle_connect)

        self.connect_btn = QPushButton("Share")
        self.connect_btn.clicked.connect(self.toggle_connect)

        ip_row.addWidget(self.device_combo, 1)
        ip_row.addWidget(self.connect_btn)
        self.card_layout.addLayout(ip_row)

        # Row 2: Live-Adjustable FPS & Ultra-Quality Preset Selector
        qual_row = QHBoxLayout()
        self.fps_combo = QComboBox()
        self.fps_combo.addItems(["60 FPS", "30 FPS", "120 FPS", "15 FPS"])
        self.fps_combo.setCurrentIndex(0)
        self.fps_combo.currentIndexChanged.connect(self.on_fps_changed)
        self.fps_combo.setToolTip("Framerate can be modified live at any time without stopping.")

        self.quality_combo = QComboBox()
        self.quality_combo.addItems(["Ultra Crisp (98% 4:4:4)", "High Quality (90%)", "Balanced (75%)"])
        self.quality_combo.setCurrentIndex(0)
        self.quality_combo.currentIndexChanged.connect(self.on_quality_changed)
        self.quality_combo.setToolTip("Ultra Crisp preserves 4:4:4 full color resolution for razor-sharp text.")

        qual_row.addWidget(self.fps_combo)
        qual_row.addWidget(self.quality_combo)
        self.card_layout.addLayout(qual_row)

        # Row 3: Auto-connect & Touch Input toggles + Optional PIN input
        auto_row = QHBoxLayout()
        self.auto_connect_cb = QCheckBox("Auto-Connect")
        self.auto_connect_cb.setChecked(True)

        self.touch_input_cb = QCheckBox("TV Touch Control")
        self.touch_input_cb.setChecked(True)
        self.touch_input_cb.setToolTip("When enabled, touching the TV screen controls this PC.")
        self.touch_input_cb.toggled.connect(self.on_touch_input_toggled)

        self.pin_input = QLineEdit()
        self.pin_input.setPlaceholderText("PIN (if req.)")
        self.pin_input.setMaxLength(4)
        self.pin_input.setFixedWidth(90)
        self.pin_input.returnPressed.connect(self.toggle_connect)
        self.pin_input.textChanged.connect(self.on_pin_text_changed)

        auto_row.addWidget(self.auto_connect_cb)
        auto_row.addWidget(self.touch_input_cb)
        auto_row.addStretch()
        auto_row.addWidget(self.pin_input)
        self.card_layout.addLayout(auto_row)

        # Row 4: Action Buttons (Pause/Resume, TV Audio Mute, Host Speaker Mute)
        btn_row = QHBoxLayout()
        self.pause_btn = QPushButton("⏸ Pause")
        self.pause_btn.clicked.connect(self.toggle_pause)
        self.pause_btn.setEnabled(False)

        self.stream_mute_btn = QPushButton("🔊 TV Audio")
        self.stream_mute_btn.clicked.connect(self.toggle_stream_mute)
        self.stream_mute_btn.setEnabled(False)

        self.host_mute_btn = QPushButton("🔇 Mute Host")
        self.host_mute_btn.setToolTip("Mutes local PC speakers so audio only plays through the TV")
        self.host_mute_btn.clicked.connect(self.toggle_host_mute)

        btn_row.addWidget(self.pause_btn)
        btn_row.addWidget(self.stream_mute_btn)
        btn_row.addWidget(self.host_mute_btn)
        self.card_layout.addLayout(btn_row)

        # Row 5: TV Volume Slider
        tv_vol_row = QHBoxLayout()
        tv_vol_lbl_title = QLabel("TV Vol:")
        tv_vol_lbl_title.setFixedWidth(46)
        self.tv_vol_slider = QSlider(Qt.Horizontal)
        self.tv_vol_slider.setRange(0, 150)
        self.tv_vol_slider.setValue(100)
        self.tv_vol_slider.valueChanged.connect(self.on_tv_volume_changed)

        self.tv_vol_val_lbl = QLabel("100%")
        self.tv_vol_val_lbl.setFixedWidth(34)
        self.tv_vol_val_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        tv_vol_row.addWidget(tv_vol_lbl_title)
        tv_vol_row.addWidget(self.tv_vol_slider)
        tv_vol_row.addWidget(self.tv_vol_val_lbl)
        self.card_layout.addLayout(tv_vol_row)

        # Row 6: Opacity Slider
        trans_row = QHBoxLayout()
        op_lbl_title = QLabel("Opacity:")
        op_lbl_title.setFixedWidth(46)
        self.opacity_slider = QSlider(Qt.Horizontal)
        self.opacity_slider.setRange(20, 100)
        self.opacity_slider.setValue(94)
        self.opacity_slider.valueChanged.connect(
            lambda v: self.setWindowOpacity(v / 100.0) if not self.is_mini_mode else None
        )
        self.op_val_lbl = QLabel("94%")
        self.op_val_lbl.setFixedWidth(34)
        self.op_val_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.opacity_slider.valueChanged.connect(lambda v: self.op_val_lbl.setText(f"{v}%"))

        trans_row.addWidget(op_lbl_title)
        trans_row.addWidget(self.opacity_slider)
        trans_row.addWidget(self.op_val_lbl)
        self.card_layout.addLayout(trans_row)

        # -------------------------------------------------------------------
        # View 2: Collapsed Mini Pill Indicator (48x16 Hitbox, 30x5 Bar)
        # -------------------------------------------------------------------
        self.mini_container = QFrame()
        self.mini_container.setObjectName("mini_container")
        self.mini_container.setFixedSize(48, 16)
        self.mini_container.setCursor(Qt.PointingHandCursor)
        self.mini_container.setToolTip(
            "MrCoopersScreenShare (Ctrl+Click: Pause/Resume | Click: Expand | Drag: Move)"
        )

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

        if sys.platform == "win32":
            self.is_host_muted = HostAudioController.get_host_mute()
            self._update_host_mute_ui()

    def _populate_device_list(self):
        self.device_combo.blockSignals(True)
        self.device_combo.clear()

        devices = self.history_data.get("devices", {})
        last_ip = self.history_data.get("last_ip", "").strip()

        matched_index = -1
        idx = 0
        for ip, name in devices.items():
            label = f"{name} ({ip})" if name else ip
            self.device_combo.addItem(label, ip)
            if ip == last_ip:
                matched_index = idx
            idx += 1

        if matched_index >= 0:
            self.device_combo.setCurrentIndex(matched_index)
        elif last_ip:
            self.device_combo.setEditText(last_ip)

        self.device_combo.blockSignals(False)

    def get_selected_target_ip(self) -> str:
        raw_text = self.device_combo.currentText().strip()
        data_val = self.device_combo.currentData()
        if data_val:
            return str(data_val).strip()

        if "(" in raw_text and ")" in raw_text:
            return raw_text.split("(")[-1].replace(")", "").strip()
        return raw_text

    def _update_mini_bar_style(self, override_color: Optional[str] = None):
        color = override_color or self.status_color
        self.mini_container.setStyleSheet("QFrame#mini_container { background: transparent; }")
        self.mini_bar.setStyleSheet(
            f"""
            QFrame#mini_bar {{
                background-color: {color};
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

    def expand_window(self):
        self.is_mini_mode = False
        if hasattr(self, "pulse_timer") and self.pulse_timer.isActive():
            self.pulse_timer.stop()
        self.setMinimumSize(0, 0)
        self.setMaximumSize(16777215, 16777215)
        self.stack.setCurrentWidget(self.card)
        if hasattr(self, "opacity_slider"):
            self.setWindowOpacity(self.opacity_slider.value() / 100.0)
        self.card.adjustSize()
        self.adjustSize()
        self.enforce_always_on_top()

    def prompt_set_friendly_name(self):
        current_ip = self.get_selected_target_ip()
        if not current_ip:
            return

        devices = self.history_data.get("devices", {})
        existing_name = devices.get(current_ip, "")

        new_name, ok = QInputDialog.getText(
            self,
            "Set Friendly TV Name",
            f"Enter friendly name for display IP ({current_ip}):",
            QLineEdit.Normal,
            existing_name,
        )

        if ok and new_name.strip():
            devices[current_ip] = new_name.strip()
            self.history_data["devices"] = devices
            self.history_data["last_ip"] = current_ip
            save_history(self.history_data)
            self._populate_device_list()

    def remove_selected_device(self):
        current_ip = self.get_selected_target_ip()
        devices = self.history_data.get("devices", {})
        if current_ip in devices:
            del devices[current_ip]
            self.history_data["devices"] = devices
            save_history(self.history_data)
            self._populate_device_list()

    def ensure_control_channel(self, target_ip: str) -> bool:
        """Ensures a dedicated control channel is running to the target receiver."""
        if not self.control_thread or not self.control_thread.isRunning():
            self.control_thread = InputReceiverThread(target_ip, self.is_input_enabled)
            self.control_thread.start()
            for _ in range(20):
                if self.control_thread.sock:
                    break
                time.sleep(0.05)
        return bool(self.control_thread and self.control_thread.isRunning())

    def send_receiver_window_command(self, action: str):
        target_ip = self.get_selected_target_ip() or self.discovered_ip
        if target_ip and self.ensure_control_channel(target_ip):
            self.control_thread.send_command({"type": "window_control", "action": action})

    def open_timer_dialog(self):
        dlg = TimerDialog(self)
        if dlg.exec() == QDialog.Accepted:
            args = dlg.get_args()
            if args:
                self.send_receiver_timer(args)

    def send_receiver_timer(self, args: str):
        """Sends clean duration/argument string directly to receiver's linked timer."""
        target_ip = self.get_selected_target_ip() or self.discovered_ip
        clean_args = args.strip().strip('"').strip("'")
        if target_ip and self.ensure_control_channel(target_ip):
            self.control_thread.send_command({"type": "timer", "args": clean_args})

    # --- Interactive Remote TV Control (Keeps control channel independent) ---
    def open_receiver_viewer(self):
        target_ip = self.get_selected_target_ip() or self.discovered_ip
        if not target_ip:
            return

        self.ensure_control_channel(target_ip)

        # Isolate TV Display: Pause forward video to eliminate mirror loop without dropping the control channel
        if self.stream_thread and self.stream_thread.isRunning() and not self.stream_thread.paused:
            self.was_streaming_before_viewing = True
            self.stream_thread.paused = True
        else:
            self.was_streaming_before_viewing = False

        # Lower receiver canvas to reveal remote desktop
        if self.control_thread:
            self.control_thread.send_command({"type": "window_control", "action": "minimize"})

        def send_remote_cmd(cmd: dict):
            if self.ensure_control_channel(target_ip):
                self.control_thread.send_command(cmd)

        if self.viewer_window:
            try:
                self.viewer_window.close()
            except Exception:
                pass

        self.viewer_window = RemoteReceiverViewerWindow(target_ip, send_remote_cmd)
        self.viewer_window.viewer_closed.connect(self._on_viewer_closed)
        self.viewer_window.show()

    def _on_viewer_closed(self):
        target_ip = self.get_selected_target_ip() or self.discovered_ip
        if target_ip and self.ensure_control_channel(target_ip):
            self.control_thread.send_command({"type": "window_control", "action": "maximize"})

        if self.was_streaming_before_viewing and self.stream_thread:
            self.stream_thread.paused = False

        self.viewer_window = None

    def on_touch_input_toggled(self, checked: bool):
        self.input_enabled = checked

    def is_input_enabled(self) -> bool:
        return self.input_enabled

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if not self.is_mini_mode:
                self.toggle_connect()
                event.accept()
                return
        super().keyPressEvent(event)

    def enterEvent(self, event):
        if self.is_mini_mode and not self.is_pulsing:
            self.setWindowOpacity(1.0)
        self.enforce_always_on_top()
        super().enterEvent(event)

    def leaveEvent(self, event):
        if self.is_mini_mode and not self.is_pulsing:
            self.setWindowOpacity(0.35)
            self.enforce_always_on_top()
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
                if self.is_mini_mode:
                    self.enforce_always_on_top()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            if self.is_mini_mode and not self._is_dragging:
                if event.modifiers() & Qt.ControlModifier:
                    self.toggle_pause()
                else:
                    self.expand_window()
            self.enforce_always_on_top()

    def showEvent(self, event):
        self.enforce_always_on_top()
        super().showEvent(event)

    def contextMenuEvent(self, event):
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
                padding: 6px 20px 6px 12px;
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

        target_ip = self.get_selected_target_ip() or self.discovered_ip
        has_target = bool(target_ip)

        # View & Control Receiver Screen Option
        view_rec_act = QAction("🖥️ View & Control TV Screen", self)
        view_rec_act.triggered.connect(self.open_receiver_viewer)
        view_rec_act.setEnabled(has_target)
        menu.addAction(view_rec_act)

        menu.addSeparator()

        # Show Timer Option
        timer_act = QAction("⏱️ TV Timer...", self)
        timer_act.triggered.connect(self.open_timer_dialog)
        timer_act.setEnabled(has_target)
        menu.addAction(timer_act)

        menu.addSeparator()

        # Remote Receiver Window Management
        win_menu = menu.addMenu("📺 TV Window Control")
        max_act = QAction("🗖 Maximize / Fullscreen TV", self)
        max_act.triggered.connect(lambda: self.send_receiver_window_command("maximize"))
        win_menu.addAction(max_act)

        norm_act = QAction("🗗 Restore TV Window (Normal)", self)
        norm_act.triggered.connect(lambda: self.send_receiver_window_command("normal"))
        win_menu.addAction(norm_act)

        min_act = QAction("🗕 Minimize TV Window", self)
        min_act.triggered.connect(lambda: self.send_receiver_window_command("minimize"))
        win_menu.addAction(min_act)
        win_menu.setEnabled(has_target)

        menu.addSeparator()

        # TV Friendly Name Options
        rename_act = QAction("🏷️ Set Friendly Name for TV...", self)
        rename_act.triggered.connect(self.prompt_set_friendly_name)
        menu.addAction(rename_act)

        remove_act = QAction("🗑️ Remove Selected TV", self)
        remove_act.triggered.connect(self.remove_selected_device)
        menu.addAction(remove_act)

        menu.addSeparator()

        touch_act = QAction("Allow TV Touch Input", self)
        touch_act.setCheckable(True)
        touch_act.setChecked(self.input_enabled)
        touch_act.triggered.connect(lambda c: self.touch_input_cb.setChecked(c))
        menu.addAction(touch_act)

        menu.addSeparator()

        if self.stream_thread and self.stream_thread.isRunning():
            pause_act = QAction("Resume Stream" if self.is_paused else "Pause Stream", self)
            pause_act.triggered.connect(self.toggle_pause)
            menu.addAction(pause_act)
            menu.addSeparator()

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

    def on_fps_changed(self, index: int):
        fps_map = {0: 60, 1: 30, 2: 120, 3: 15}
        chosen_fps = fps_map.get(index, 60)
        if self.stream_thread:
            self.stream_thread.set_fps_limit(chosen_fps)

    def on_quality_changed(self, index: int):
        if index == 0:
            target_quality, use_444 = 98, True
        elif index == 1:
            target_quality, use_444 = 90, True
        else:
            target_quality, use_444 = 75, False

        if self.stream_thread:
            self.stream_thread.set_quality_params(target_quality, use_444)

    def on_tv_volume_changed(self, val: int):
        self.tv_vol_val_lbl.setText(f"{val}%")
        self.tv_volume = val / 100.0
        if self.audio_thread:
            self.audio_thread.set_volume(self.tv_volume)

    def toggle_host_mute(self):
        new_state = not self.is_host_muted
        success = HostAudioController.set_host_mute(new_state)
        if success:
            self.is_host_muted = new_state
        else:
            self.is_host_muted = HostAudioController.get_host_mute()
        self._update_host_mute_ui()

    def _update_host_mute_ui(self):
        if self.is_host_muted:
            self.host_mute_btn.setText("🔊 Unmute Host")
            self.host_mute_btn.setStyleSheet("background-color: #d83b01; color: white;")
        else:
            self.host_mute_btn.setText("🔇 Mute Host")
            self.host_mute_btn.setStyleSheet("background-color: #262c3b; color: #ffffff;")

    def on_device_discovered(self, ip: str, pin_required: bool):
        self.discovered_ip = ip
        self.pin_required = pin_required

        if not self.device_combo.currentText().strip():
            self.device_combo.setEditText(ip)

        if (
            self.auto_connect_cb.isChecked()
            and (not self.stream_thread or not self.stream_thread.isRunning())
        ):
            if not pin_required or len(self.pin_input.text().strip()) == 4:
                self.start_sharing()

    def on_pin_text_changed(self, text: str):
        if (
            len(text.strip()) == 4
            and self.auto_connect_cb.isChecked()
            and (not self.stream_thread or not self.stream_thread.isRunning())
        ):
            self.start_sharing()

    def toggle_connect(self):
        if self.stream_thread and self.stream_thread.isRunning():
            self.stop_sharing()
        else:
            self.start_sharing()

    def start_sharing(self):
        target_ip = self.get_selected_target_ip() or self.discovered_ip
        if not target_ip:
            return

        fps_map = {0: 60, 1: 30, 2: 120, 3: 15}
        chosen_fps = fps_map.get(self.fps_combo.currentIndex(), 60)
        pin_code = self.pin_input.text().strip()

        quality_idx = self.quality_combo.currentIndex()
        if quality_idx == 0:
            target_quality, use_444 = 98, True
        elif quality_idx == 1:
            target_quality, use_444 = 90, True
        else:
            target_quality, use_444 = 75, False

        self.connect_btn.setText("Stop")
        self.connect_btn.setStyleSheet("background-color: #d83b01;")
        self.pause_btn.setEnabled(True)
        self.stream_mute_btn.setEnabled(True)

        self.stream_thread = ScreenSenderThread(
            target_ip=target_ip,
            pin=pin_code,
            quality=target_quality,
            fps_limit=chosen_fps,
            use_444_chroma=use_444,
        )
        self.stream_thread.status_changed.connect(self.on_stream_status)
        self.stream_thread.start()

        self.audio_thread = AudioSenderThread(target_ip, volume=self.tv_volume)
        self.audio_thread.start()

        self.ensure_control_channel(target_ip)

    def stop_sharing(self):
        if self.stream_thread:
            self.stream_thread.stop()
            self.stream_thread = None
        if self.audio_thread:
            self.audio_thread.stop()
            self.audio_thread = None

        self.is_paused = False

        if self.viewer_window:
            try:
                self.viewer_window.close()
            except Exception:
                pass
            self.viewer_window = None

        if self.control_thread and not self.viewer_window:
            self.control_thread.stop()
            self.control_thread = None

        self.connect_btn.setText("Share")
        self.connect_btn.setStyleSheet("background-color: #0078d4;")
        self.pause_btn.setText("⏸ Pause")
        self.pause_btn.setStyleSheet("background-color: #0078d4; color: white;")
        self.pause_btn.setEnabled(False)
        self.stream_mute_btn.setEnabled(False)

        self._update_status_color("#8f9bb3")

    def toggle_pause(self):
        if self.stream_thread:
            if not self.is_paused:
                self.stream_thread.trigger_cursorless_frame()
                self.is_paused = True
                self.stream_thread.paused = True
                self.pause_btn.setText("▶ Resume")
                self.pause_btn.setStyleSheet("background-color: #f37021; color: white; font-weight: bold;")
                self._update_status_color("#f37021")
            else:
                self.is_paused = False
                self.stream_thread.paused = False
                self.pause_btn.setText("⏸ Pause")
                self.pause_btn.setStyleSheet("background-color: #0078d4; color: white; font-weight: bold;")
                self._update_status_color("#00d084")

    def toggle_stream_mute(self):
        if self.audio_thread:
            self.is_stream_muted = not self.is_stream_muted
            self.audio_thread.muted = self.is_stream_muted

            if self.is_stream_muted:
                self.stream_mute_btn.setText("🔇 TV Muted")
                self.stream_mute_btn.setStyleSheet("background-color: #d83b01; color: white; font-weight: bold;")
            else:
                self.stream_mute_btn.setText("🔊 TV Audio")
                self.stream_mute_btn.setStyleSheet("background-color: #0078d4; color: white; font-weight: bold;")

    def on_stream_status(self, text: str, active: bool):
        if active:
            color = "#f37021" if self.is_paused else "#00d084"
        else:
            color = "#d83b01"
        self._update_status_color(color)

        if active:
            connected_ip = self.get_selected_target_ip() or self.discovered_ip
            if connected_ip:
                self.history_data["last_ip"] = connected_ip
                if connected_ip not in self.history_data.get("devices", {}):
                    self.history_data.setdefault("devices", {})[connected_ip] = ""
                save_history(self.history_data)
        else:
            if not self.viewer_window:
                self.stop_sharing()

    def _update_status_color(self, color_hex: str):
        self.status_color = color_hex
        self.status_dot.setStyleSheet(f"color: {color_hex}; font-size: 14px;")
        if not self.is_pulsing:
            self._update_mini_bar_style()

    def closeEvent(self, event):
        self.stop_sharing()
        if self.control_thread:
            self.control_thread.stop()
            self.control_thread = None
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