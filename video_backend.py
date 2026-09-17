#################### START OF FILE: video_backend.py ####################

"""
Hardware Mouse Cursor Coordinate Extractor, Anti-Aliased Overlay Renderer,
and Zero-Dependency Windows DirectX 11 / DXGI Desktop Duplication Hardware Grabber.
Supports Windows Win32 API, Wayland / X11 Device Pixel Ratio (DPR) fractional scaling,
and high-speed GPU framebuffer capture on Windows 10 & 11 with full verbose diagnostics.
"""

import ctypes
from ctypes import (
    POINTER,
    Structure,
    byref,
    c_int,
    c_int64,
    c_long,
    c_ubyte,
    c_uint,
    c_ulong,
    c_ushort,
    c_void_p,
    c_wchar,
)
import sys
import time
from typing import Optional

import cv2
import numpy as np
from PySide6.QtGui import QCursor, QGuiApplication

HRESULT = getattr(ctypes, "HRESULT", ctypes.c_long)
WINFUNCTYPE = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)


class GUID(Structure):
    _fields_ = [
        ("Data1", c_ulong),
        ("Data2", c_ushort),
        ("Data3", c_ushort),
        ("Data4", c_ubyte * 8),
    ]

    def __init__(self, l, w1, w2, b1, b2, b3, b4, b5, b6, b7, b8):
        super().__init__(l, w1, w2, (c_ubyte * 8)(b1, b2, b3, b4, b5, b6, b7, b8))


IID_IDXGIDevice = GUID(
    0x54EC77FA, 0x1377, 0x44E6, 0x8C, 0x32, 0x88, 0xFD, 0x5F, 0x44, 0xC8, 0x4C
)
IID_IDXGIOutput1 = GUID(
    0x00CDDEA8, 0x939B, 0x4B83, 0xA3, 0x4D, 0x70, 0x59, 0x32, 0xF5, 0x76, 0xC0
)
IID_ID3D11Texture2D = GUID(
    0x6F15AAF2, 0xD208, 0x4E89, 0x9A, 0xB4, 0x48, 0x95, 0x35, 0xD3, 0x4F, 0x9C
)


def _release_com_ptr(ptr: c_void_p):
    if ptr and ptr.value:
        try:
            vtbl = ctypes.cast(ptr, POINTER(POINTER(c_void_p))).contents
            release_func = WINFUNCTYPE(c_ulong, c_void_p)(vtbl[2])
            release_func(ptr)
        except Exception:
            pass


class RECT(Structure):
    _fields_ = [
        ("left", c_long),
        ("top", c_long),
        ("right", c_long),
        ("bottom", c_long),
    ]


class DXGI_OUTPUT_DESC(Structure):
    _fields_ = [
        ("DeviceName", c_wchar * 32),
        ("DesktopCoordinates", RECT),
        ("AttachedToDesktop", c_int),
        ("Rotation", c_uint),
        ("Monitor", c_void_p),
    ]


class D3D11_TEXTURE2D_DESC(Structure):
    _fields_ = [
        ("Width", c_uint),
        ("Height", c_uint),
        ("MipLevels", c_uint),
        ("ArraySize", c_uint),
        ("Format", c_uint),
        ("SampleDesc_Count", c_uint),
        ("SampleDesc_Quality", c_uint),
        ("Usage", c_uint),
        ("BindFlags", c_uint),
        ("CPUAccessFlags", c_uint),
        ("MiscFlags", c_uint),
    ]


class D3D11_MAPPED_SUBRESOURCE(Structure):
    _fields_ = [
        ("pData", c_void_p),
        ("RowPitch", c_uint),
        ("DepthPitch", c_uint),
    ]


class POINT_L(Structure):
    _fields_ = [("x", c_long), ("y", c_long)]


class DXGI_OUTDUPL_POINTER_POSITION(Structure):
    _fields_ = [
        ("Position", POINT_L),
        ("Visible", c_int),
    ]


class DXGI_OUTDUPL_POINTER_SHAPE_INFO(Structure):
    _fields_ = [
        ("Type", c_uint),
        ("Width", c_uint),
        ("Height", c_uint),
        ("Pitch", c_uint),
        ("HotSpot", POINT_L),
    ]


class DXGI_OUTDUPL_FRAME_INFO(Structure):
    _fields_ = [
        ("LastPresentTime", c_int64),
        ("LastMouseUpdateTime", c_int64),
        ("TotalMetadataBuffersSize", c_uint),
        ("MouseMoved", c_int),
        ("PointerPosition", DXGI_OUTDUPL_POINTER_POSITION),
        ("RectsCoalesced", c_uint),
        ("ProtectedContentMaskedOut", c_int),
        ("PointerShapeInfo", DXGI_OUTDUPL_POINTER_SHAPE_INFO),
    ]


class WindowsDXGIGrabber:
    """Zero-dependency hardware GPU DirectX 11 / DXGI Desktop Duplication capture for Windows 10 & 11."""

    def __init__(self, output_index: int = 0):
        self.available = False
        self.output_index = output_index
        self.width = 0
        self.height = 0
        self.mon_left = 0
        self.mon_top = 0
        self.last_frame: Optional[np.ndarray] = None
        self.first_frame_logged = False

        self.p_device = c_void_p()
        self.p_context = c_void_p()
        self.p_duplication = c_void_p()
        self.p_staging_tex = c_void_p()

        if sys.platform == "win32":
            print(f"[DXGI-Init] Initializing DirectX 11 Desktop Duplication (Output {output_index})...", flush=True)
            self.available = self._initialize()
            if self.available:
                print(
                    f"[DXGI-Init] SUCCESS: Hardware GPU Desktop Duplication ACTIVE ({self.width}x{self.height} at {self.mon_left},{self.mon_top})",
                    flush=True,
                )
            else:
                print("[DXGI-Init] WARNING: DXGI hardware capture unavailable. Falling back to MSS GDI.", flush=True)

    def _initialize(self) -> bool:
        self._cleanup()

        p_adapter = c_void_p()
        p_dxgi_dev = c_void_p()
        p_output = c_void_p()
        p_output1 = c_void_p()

        try:
            feature_level = c_uint(0)
            hr = ctypes.windll.d3d11.D3D11CreateDevice(
                None,
                1,  # D3D_DRIVER_TYPE_HARDWARE
                None,
                0,
                None,
                0,
                7,  # D3D11_SDK_VERSION
                byref(self.p_device),
                byref(feature_level),
                byref(self.p_context),
            )

            if hr != 0 or not self.p_device.value:
                print(f"[DXGI-Init] Hardware device creation failed (HRESULT 0x{hr & 0xFFFFFFFF:08X}), trying WARP...", flush=True)
                hr = ctypes.windll.d3d11.D3D11CreateDevice(
                    None,
                    2,  # D3D_DRIVER_TYPE_WARP
                    None,
                    0,
                    None,
                    0,
                    7,
                    byref(self.p_device),
                    byref(feature_level),
                    byref(self.p_context),
                )
                if hr != 0 or not self.p_device.value:
                    print(f"[DXGI-Init] D3D11 WARP creation failed: HRESULT 0x{hr & 0xFFFFFFFF:08X}", flush=True)
                    return False

            dev_vtbl = ctypes.cast(
                self.p_device, POINTER(POINTER(c_void_p))
            ).contents
            dev_qi = WINFUNCTYPE(
                HRESULT, c_void_p, POINTER(GUID), POINTER(c_void_p)
            )(dev_vtbl[0])

            hr = dev_qi(self.p_device, byref(IID_IDXGIDevice), byref(p_dxgi_dev))
            if hr != 0 or not p_dxgi_dev.value:
                print(f"[DXGI-Init] QueryInterface IDXGIDevice failed: 0x{hr & 0xFFFFFFFF:08X}", flush=True)
                return False

            dxgi_vtbl = ctypes.cast(
                p_dxgi_dev, POINTER(POINTER(c_void_p))
            ).contents
            get_adapter = WINFUNCTYPE(HRESULT, c_void_p, POINTER(c_void_p))(
                dxgi_vtbl[7]
            )
            hr = get_adapter(p_dxgi_dev, byref(p_adapter))
            if hr != 0 or not p_adapter.value:
                print(f"[DXGI-Init] GetAdapter failed: 0x{hr & 0xFFFFFFFF:08X}", flush=True)
                return False

            adapter_vtbl = ctypes.cast(
                p_adapter, POINTER(POINTER(c_void_p))
            ).contents
            enum_outputs = WINFUNCTYPE(
                HRESULT, c_void_p, c_uint, POINTER(c_void_p)
            )(adapter_vtbl[7])

            hr = enum_outputs(p_adapter, self.output_index, byref(p_output))
            if hr != 0 or not p_output.value:
                if self.output_index != 0:
                    hr = enum_outputs(p_adapter, 0, byref(p_output))
                if hr != 0 or not p_output.value:
                    print(f"[DXGI-Init] EnumOutputs failed: 0x{hr & 0xFFFFFFFF:08X}", flush=True)
                    return False

            out_vtbl = ctypes.cast(
                p_output, POINTER(POINTER(c_void_p))
            ).contents
            get_desc = WINFUNCTYPE(
                HRESULT, c_void_p, POINTER(DXGI_OUTPUT_DESC)
            )(out_vtbl[7])
            desc = DXGI_OUTPUT_DESC()
            hr = get_desc(p_output, byref(desc))
            if hr == 0:
                self.mon_left = int(desc.DesktopCoordinates.left)
                self.mon_top = int(desc.DesktopCoordinates.top)
                self.width = int(
                    desc.DesktopCoordinates.right - desc.DesktopCoordinates.left
                )
                self.height = int(
                    desc.DesktopCoordinates.bottom - desc.DesktopCoordinates.top
                )

            if self.width <= 0 or self.height <= 0:
                self.width = ctypes.windll.user32.GetSystemMetrics(0)
                self.height = ctypes.windll.user32.GetSystemMetrics(1)

            out_qi = WINFUNCTYPE(
                HRESULT, c_void_p, POINTER(GUID), POINTER(c_void_p)
            )(out_vtbl[0])
            hr = out_qi(p_output, byref(IID_IDXGIOutput1), byref(p_output1))
            if hr != 0 or not p_output1.value:
                print(f"[DXGI-Init] QueryInterface IDXGIOutput1 failed: 0x{hr & 0xFFFFFFFF:08X}", flush=True)
                return False

            out1_vtbl = ctypes.cast(
                p_output1, POINTER(POINTER(c_void_p))
            ).contents

            # IDXGIOutput1::DuplicateOutput is index 19 (IUnknown=3, IDXGIObject=4, IDXGIDeviceSubObject=1, IDXGIOutput=10, IDXGIOutput1=1)
            dup_output = WINFUNCTYPE(
                HRESULT, c_void_p, c_void_p, POINTER(c_void_p)
            )(out1_vtbl[19])

            hr = dup_output(p_output1, self.p_device, byref(self.p_duplication))
            if hr != 0 or not self.p_duplication.value:
                print(f"[DXGI-Init] DuplicateOutput call failed: 0x{hr & 0xFFFFFFFF:08X}", flush=True)
                return False

            tex_desc = D3D11_TEXTURE2D_DESC(
                self.width,
                self.height,
                1,
                1,
                87,  # DXGI_FORMAT_B8G8R8A8_UNORM
                1,
                0,
                3,  # D3D11_USAGE_STAGING
                0,
                0x20000,  # D3D11_CPU_ACCESS_READ
                0,
            )

            create_tex = WINFUNCTYPE(
                HRESULT,
                c_void_p,
                POINTER(D3D11_TEXTURE2D_DESC),
                c_void_p,
                POINTER(c_void_p),
            )(dev_vtbl[5])

            hr = create_tex(
                self.p_device, byref(tex_desc), None, byref(self.p_staging_tex)
            )
            if hr != 0 or not self.p_staging_tex.value:
                print(f"[DXGI-Init] CreateTexture2D staging texture failed: 0x{hr & 0xFFFFFFFF:08X}", flush=True)
                return False

            return True
        except Exception as ex:
            print(f"[DXGI-Init] Exception during init: {ex}", flush=True)
            return False
        finally:
            _release_com_ptr(p_output1)
            _release_com_ptr(p_output)
            _release_com_ptr(p_adapter)
            _release_com_ptr(p_dxgi_dev)

    def grab(self) -> Optional[np.ndarray]:
        if not self.available or not self.p_duplication.value:
            return self.last_frame

        p_resource = c_void_p()
        p_desktop_tex = c_void_p()
        frame_info = DXGI_OUTDUPL_FRAME_INFO()

        try:
            dup_vtbl = ctypes.cast(
                self.p_duplication, POINTER(POINTER(c_void_p))
            ).contents
            acquire_func = WINFUNCTYPE(
                HRESULT,
                c_void_p,
                c_uint,
                POINTER(DXGI_OUTDUPL_FRAME_INFO),
                POINTER(c_void_p),
            )(dup_vtbl[8])
            release_frame_func = WINFUNCTYPE(HRESULT, c_void_p)(dup_vtbl[14])

            # Wait up to 10 ms for GPU buffer presentation
            hr = acquire_func(
                self.p_duplication, 10, byref(frame_info), byref(p_resource)
            )

            DXGI_ERROR_WAIT_TIMEOUT = -2005270521  # 0x887A0027
            DXGI_ERROR_ACCESS_LOST = -2005270522  # 0x887A0026

            if hr == DXGI_ERROR_WAIT_TIMEOUT:
                return self.last_frame

            if hr == DXGI_ERROR_ACCESS_LOST or hr != 0 or not p_resource.value:
                if hr == DXGI_ERROR_ACCESS_LOST:
                    self.available = self._initialize()
                return self.last_frame

            res_vtbl = ctypes.cast(
                p_resource, POINTER(POINTER(c_void_p))
            ).contents
            res_qi = WINFUNCTYPE(
                HRESULT, c_void_p, POINTER(GUID), POINTER(c_void_p)
            )(res_vtbl[0])

            hr = res_qi(
                p_resource, byref(IID_ID3D11Texture2D), byref(p_desktop_tex)
            )
            if hr != 0 or not p_desktop_tex.value:
                release_frame_func(self.p_duplication)
                _release_com_ptr(p_resource)
                return self.last_frame

            ctx_vtbl = ctypes.cast(
                self.p_context, POINTER(POINTER(c_void_p))
            ).contents
            copy_resource = WINFUNCTYPE(
                None, c_void_p, c_void_p, c_void_p
            )(ctx_vtbl[47])
            copy_resource(self.p_context, self.p_staging_tex, p_desktop_tex)

            _release_com_ptr(p_desktop_tex)
            _release_com_ptr(p_resource)

            map_func = WINFUNCTYPE(
                HRESULT,
                c_void_p,
                c_void_p,
                c_uint,
                c_uint,
                c_uint,
                POINTER(D3D11_MAPPED_SUBRESOURCE),
            )(ctx_vtbl[14])
            unmap_func = WINFUNCTYPE(None, c_void_p, c_uint)(ctx_vtbl[15])

            mapped = D3D11_MAPPED_SUBRESOURCE()
            hr = map_func(self.p_context, self.p_staging_tex, 0, 1, 0, byref(mapped))
            if hr != 0 or not mapped.pData:
                release_frame_func(self.p_duplication)
                return self.last_frame

            pitch = mapped.RowPitch
            src_ptr = mapped.pData
            buf_len = pitch * self.height

            ubuf = (c_ubyte * buf_len).from_address(src_ptr)
            raw_arr = np.frombuffer(ubuf, dtype=np.uint8).reshape(
                (self.height, pitch)
            )

            expected_line = self.width * 4
            if pitch == expected_line:
                frame_bgra = raw_arr.reshape((self.height, self.width, 4)).copy()
            else:
                frame_bgra = raw_arr[:, :expected_line].reshape(
                    (self.height, self.width, 4)
                ).copy()

            unmap_func(self.p_context, 0)
            release_frame_func(self.p_duplication)

            if not self.first_frame_logged:
                print(f"[DXGI-Perf] First hardware GPU frame captured successfully ({self.width}x{self.height})", flush=True)
                self.first_frame_logged = True

            self.last_frame = frame_bgra
            return frame_bgra

        except Exception as ex:
            _release_com_ptr(p_desktop_tex)
            _release_com_ptr(p_resource)
            return self.last_frame

    def _cleanup(self):
        _release_com_ptr(self.p_staging_tex)
        _release_com_ptr(self.p_duplication)
        _release_com_ptr(self.p_context)
        _release_com_ptr(self.p_device)
        self.p_staging_tex = c_void_p()
        self.p_duplication = c_void_p()
        self.p_context = c_void_p()
        self.p_device = c_void_p()

    def close(self):
        self.available = False
        self._cleanup()


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

    if scale_factor <= 0.0 or scale_factor == 1.0:
        screen = QGuiApplication.primaryScreen()
        if screen:
            scale_factor = float(screen.devicePixelRatio())
        else:
            scale_factor = 1.0

    if sys.platform == "win32":
        cx = int(round(gx - monitor_left))
        cy = int(round(gy - monitor_top))
    else:
        cx = int(round((gx - monitor_left) * scale_factor))
        cy = int(round((gy - monitor_top) * scale_factor))

    h, w = bgr_image.shape[:2]
    if 0 <= cx < w and 0 <= cy < h:
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

        is_4ch = bgr_image.ndim == 3 and bgr_image.shape[2] == 4
        col_black = (0, 0, 0, 255) if is_4ch else (0, 0, 0)
        col_white = (255, 255, 255, 255) if is_4ch else (255, 255, 255)
        col_dark = (20, 20, 20, 255) if is_4ch else (20, 20, 20)

        # Outer high-contrast black border
        cv2.polylines(
            bgr_image,
            [pts],
            isClosed=True,
            color=col_black,
            thickness=max(2, int(round(2 * size_mult))),
            lineType=cv2.LINE_AA,
        )
        # Inner white fill
        cv2.fillPoly(
            bgr_image,
            [pts],
            color=col_white,
            lineType=cv2.LINE_AA,
        )
        # Inner accent line
        cv2.polylines(
            bgr_image,
            [pts],
            isClosed=True,
            color=col_dark,
            thickness=1,
            lineType=cv2.LINE_AA,
        )