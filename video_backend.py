# video_backend.py

"""
Hardware Mouse Cursor Coordinate Extractor, Anti-Aliased Overlay Renderer,
Zero-Dependency Windows DirectX 11 / DXGI Desktop Duplication Hardware Grabber,
and Persistent Win32 DIB Section Fast GDI Fallback Grabber.
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
    sizeof,
)
import sys
import time
from typing import Optional

import cv2
import numpy as np
from PySide6.QtGui import QCursor, QGuiApplication

WINFUNCTYPE = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)


class GUID(Structure):
    _fields_ = [
        ("Data1", c_ulong),
        ("Data2", c_ushort),
        ("Data3", c_ushort),
        ("Data4", c_ubyte * 8),
    ]


def parse_guid(guid_str: str) -> GUID:
    """Uses native Windows ole32 API to ensure 100% byte-accurate binary GUID parsing."""
    g = GUID()
    if sys.platform == "win32":
        ctypes.windll.ole32.IIDFromString(str(guid_str), byref(g))
    return g


IID_IDXGIFactory1 = parse_guid("{770AAE78-F26F-4DBA-A829-253C83D1B387}")
IID_IDXGIDevice = parse_guid("{54EC77FA-1377-44E6-8C32-88FD5F44C84C}")
IID_IDXGIOutput1 = parse_guid("{00CDDEA8-939B-4B83-A34D-705932F576C0}")
IID_ID3D11Texture2D = parse_guid("{6F15AAF2-D208-4E89-9AB4-489535D34F9C}")
IID_IDXGIResource = parse_guid("{035F3AB4-482E-4E50-B41F-8A7F8BD8960B}")


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


class DXGI_ADAPTER_DESC1(Structure):
    _fields_ = [
        ("Description", c_wchar * 128),
        ("VendorId", c_uint),
        ("DeviceId", c_uint),
        ("SubSysId", c_uint),
        ("Revision", c_uint),
        ("DedicatedVideoMemory", ctypes.c_size_t),
        ("DedicatedSystemMemory", ctypes.c_size_t),
        ("SharedSystemMemory", ctypes.c_size_t),
        ("AdapterLuid_LowPart", c_uint),
        ("AdapterLuid_HighPart", c_long),
        ("Flags", c_uint),
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

    def __init__(self, target_monitor_index: int = 0):
        self.available = False
        self.target_monitor_index = target_monitor_index
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
            print("[DXGI-Init] Initializing DirectX 11 Desktop Duplication pipeline...", flush=True)
            self.available = self._initialize()
            if self.available:
                print(
                    f"[DXGI-Init] SUCCESS: Hardware GPU Duplication ACTIVE ({self.width}x{self.height} at {self.mon_left},{self.mon_top})",
                    flush=True,
                )
            else:
                print("[DXGI-Init] DXGI hardware capture unavailable on current adapter.", flush=True)

    def _initialize(self) -> bool:
        self._cleanup()

        p_factory = c_void_p()
        p_adapter = c_void_p()
        p_output = c_void_p()
        p_output1 = c_void_p()

        try:
            hr = ctypes.windll.dxgi.CreateDXGIFactory1(
                byref(IID_IDXGIFactory1), byref(p_factory)
            )
            if hr != 0 or not p_factory.value:
                print(f"[DXGI-Init] CreateDXGIFactory1 failed: 0x{hr & 0xFFFFFFFF:08X}", flush=True)
                return False

            factory_vtbl = ctypes.cast(
                p_factory, POINTER(POINTER(c_void_p))
            ).contents
            enum_adapters1 = WINFUNCTYPE(c_long, c_void_p, c_uint, POINTER(c_void_p))(
                factory_vtbl[12]
            )

            matched_adapter = None
            matched_output = None
            matched_desc = None

            cur_mon_idx = 0
            adapter_idx = 0

            while True:
                curr_adapter_p = c_void_p()
                hr = enum_adapters1(p_factory, adapter_idx, byref(curr_adapter_p))
                if hr != 0 or not curr_adapter_p.value:
                    break

                adapter_vtbl = ctypes.cast(
                    curr_adapter_p, POINTER(POINTER(c_void_p))
                ).contents
                get_desc1 = WINFUNCTYPE(c_long, c_void_p, POINTER(DXGI_ADAPTER_DESC1))(
                    adapter_vtbl[10]
                )
                a_desc = DXGI_ADAPTER_DESC1()
                get_desc1(curr_adapter_p, byref(a_desc))

                enum_outputs = WINFUNCTYPE(c_long, c_void_p, c_uint, POINTER(c_void_p))(
                    adapter_vtbl[7]
                )

                output_idx = 0
                while True:
                    curr_out_p = c_void_p()
                    hr = enum_outputs(curr_adapter_p, output_idx, byref(curr_out_p))
                    if hr != 0 or not curr_out_p.value:
                        break

                    out_vtbl = ctypes.cast(
                        curr_out_p, POINTER(POINTER(c_void_p))
                    ).contents
                    get_out_desc = WINFUNCTYPE(c_long, c_void_p, POINTER(DXGI_OUTPUT_DESC))(
                        out_vtbl[7]
                    )
                    o_desc = DXGI_OUTPUT_DESC()
                    hr_desc = get_out_desc(curr_out_p, byref(o_desc))

                    if hr_desc == 0 and o_desc.AttachedToDesktop:
                        w = int(o_desc.DesktopCoordinates.right - o_desc.DesktopCoordinates.left)
                        h = int(o_desc.DesktopCoordinates.bottom - o_desc.DesktopCoordinates.top)
                        print(
                            f"[DXGI-Init] Found Monitor {cur_mon_idx}: '{o_desc.DeviceName}' on GPU '{a_desc.Description}' ({w}x{h})",
                            flush=True,
                        )

                        if cur_mon_idx == self.target_monitor_index:
                            matched_adapter = curr_adapter_p
                            matched_output = curr_out_p
                            matched_desc = o_desc
                            break

                        cur_mon_idx += 1

                    _release_com_ptr(curr_out_p)
                    output_idx += 1

                if matched_adapter:
                    break

                _release_com_ptr(curr_adapter_p)
                adapter_idx += 1

            if not matched_adapter or not matched_output:
                print("[DXGI-Init] No attached desktop output matched target monitor index.", flush=True)
                return False

            p_adapter = matched_adapter
            p_output = matched_output
            self.mon_left = int(matched_desc.DesktopCoordinates.left)
            self.mon_top = int(matched_desc.DesktopCoordinates.top)
            self.width = int(matched_desc.DesktopCoordinates.right - matched_desc.DesktopCoordinates.left)
            self.height = int(matched_desc.DesktopCoordinates.bottom - matched_desc.DesktopCoordinates.top)

            # Create D3D11 device first so driver extensions are fully activated
            feature_level = c_uint(0)
            D3D11_SDK_VERSION = 7
            D3D11_CREATE_DEVICE_BGRA_SUPPORT = 0x20
            hr = ctypes.windll.d3d11.D3D11CreateDevice(
                p_adapter,
                0,
                None,
                D3D11_CREATE_DEVICE_BGRA_SUPPORT,
                None,
                0,
                D3D11_SDK_VERSION,
                byref(self.p_device),
                byref(feature_level),
                byref(self.p_context),
            )
            if hr != 0 or not self.p_device.value:
                hr = ctypes.windll.d3d11.D3D11CreateDevice(
                    p_adapter,
                    0,
                    None,
                    0,
                    None,
                    0,
                    D3D11_SDK_VERSION,
                    byref(self.p_device),
                    byref(feature_level),
                    byref(self.p_context),
                )
                if hr != 0 or not self.p_device.value:
                    print(f"[DXGI-Init] D3D11CreateDevice for adapter failed: 0x{hr & 0xFFFFFFFF:08X}", flush=True)
                    return False

            # Query IDXGIOutput1 using native parsed GUID
            out_vtbl = ctypes.cast(p_output, POINTER(POINTER(c_void_p))).contents
            out_qi = WINFUNCTYPE(c_long, c_void_p, POINTER(GUID), POINTER(c_void_p))(out_vtbl[0])
            hr = out_qi(p_output, byref(IID_IDXGIOutput1), byref(p_output1))
            if hr != 0 or not p_output1.value:
                # If standalone output lacks DXGI 1.2 on this driver, query via D3D11 device adapter
                p_dxgi_dev = c_void_p()
                dev_vtbl_tmp = ctypes.cast(self.p_device, POINTER(POINTER(c_void_p))).contents
                dev_qi = WINFUNCTYPE(c_long, c_void_p, POINTER(GUID), POINTER(c_void_p))(dev_vtbl_tmp[0])
                hr_dev = dev_qi(self.p_device, byref(IID_IDXGIDevice), byref(p_dxgi_dev))
                if hr_dev == 0 and p_dxgi_dev.value:
                    dxgi_dev_vtbl = ctypes.cast(p_dxgi_dev, POINTER(POINTER(c_void_p))).contents
                    get_adapter_func = WINFUNCTYPE(c_long, c_void_p, POINTER(c_void_p))(dxgi_dev_vtbl[7])
                    p_dev_adapter = c_void_p()
                    hr_ad = get_adapter_func(p_dxgi_dev, byref(p_dev_adapter))
                    if hr_ad == 0 and p_dev_adapter.value:
                        dev_ad_vtbl = ctypes.cast(p_dev_adapter, POINTER(POINTER(c_void_p))).contents
                        dev_enum_out = WINFUNCTYPE(c_long, c_void_p, c_uint, POINTER(c_void_p))(dev_ad_vtbl[7])
                        p_dev_out = c_void_p()
                        hr_o = dev_enum_out(p_dev_adapter, output_idx, byref(p_dev_out))
                        if hr_o == 0 and p_dev_out.value:
                            d_out_vtbl = ctypes.cast(p_dev_out, POINTER(POINTER(c_void_p))).contents
                            d_out_qi = WINFUNCTYPE(c_long, c_void_p, POINTER(GUID), POINTER(c_void_p))(d_out_vtbl[0])
                            hr = d_out_qi(p_dev_out, byref(IID_IDXGIOutput1), byref(p_output1))
                            _release_com_ptr(p_dev_out)
                        _release_com_ptr(p_dev_adapter)
                    _release_com_ptr(p_dxgi_dev)

            if hr != 0 or not p_output1.value:
                print(f"[DXGI-Init] IDXGIOutput1 QueryInterface failed: 0x{hr & 0xFFFFFFFF:08X}", flush=True)
                return False

            out1_vtbl = ctypes.cast(p_output1, POINTER(POINTER(c_void_p))).contents
            # DuplicateOutput is method 20 of IDXGIOutput1
            dup_output = WINFUNCTYPE(c_long, c_void_p, c_void_p, POINTER(c_void_p))(out1_vtbl[20])
            hr = dup_output(p_output1, self.p_device, byref(self.p_duplication))
            if hr != 0 or not self.p_duplication.value:
                print(f"[DXGI-Init] DuplicateOutput failed: 0x{hr & 0xFFFFFFFF:08X}", flush=True)
                return False

            tex_desc = D3D11_TEXTURE2D_DESC(
                self.width,
                self.height,
                1,
                1,
                87,  # DXGI_FORMAT_B8G8R8A8_UNORM
                1,
                0,
                3,   # D3D11_USAGE_STAGING
                0,
                0x20000,  # D3D11_CPU_ACCESS_READ
                0,
            )

            dev_vtbl = ctypes.cast(self.p_device, POINTER(POINTER(c_void_p))).contents
            create_tex = WINFUNCTYPE(
                c_long, c_void_p, POINTER(D3D11_TEXTURE2D_DESC), c_void_p, POINTER(c_void_p)
            )(dev_vtbl[5])

            hr = create_tex(self.p_device, byref(tex_desc), None, byref(self.p_staging_tex))
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
            _release_com_ptr(p_factory)

    def grab(self) -> Optional[np.ndarray]:
        if not self.available or not self.p_duplication.value:
            return self.last_frame

        p_resource = c_void_p()
        p_desktop_tex = c_void_p()
        frame_info = DXGI_OUTDUPL_FRAME_INFO()

        try:
            dup_vtbl = ctypes.cast(self.p_duplication, POINTER(POINTER(c_void_p))).contents
            acquire_func = WINFUNCTYPE(
                c_long, c_void_p, c_uint, POINTER(DXGI_OUTDUPL_FRAME_INFO), POINTER(c_void_p)
            )(dup_vtbl[8])
            release_frame_func = WINFUNCTYPE(c_long, c_void_p)(dup_vtbl[14])

            hr = acquire_func(self.p_duplication, 16, byref(frame_info), byref(p_resource))

            DXGI_ERROR_WAIT_TIMEOUT = -2005270521
            DXGI_ERROR_ACCESS_LOST = -2005270522

            if hr == DXGI_ERROR_WAIT_TIMEOUT:
                return self.last_frame

            if hr == DXGI_ERROR_ACCESS_LOST or hr != 0 or not p_resource.value:
                if hr == DXGI_ERROR_ACCESS_LOST:
                    self.available = self._initialize()
                return self.last_frame

            res_vtbl = ctypes.cast(p_resource, POINTER(POINTER(c_void_p))).contents
            res_qi = WINFUNCTYPE(c_long, c_void_p, POINTER(GUID), POINTER(c_void_p))(res_vtbl[0])

            hr = res_qi(p_resource, byref(IID_ID3D11Texture2D), byref(p_desktop_tex))
            if hr != 0 or not p_desktop_tex.value:
                release_frame_func(self.p_duplication)
                _release_com_ptr(p_resource)
                return self.last_frame

            ctx_vtbl = ctypes.cast(self.p_context, POINTER(POINTER(c_void_p))).contents
            copy_resource = WINFUNCTYPE(None, c_void_p, c_void_p, c_void_p)(ctx_vtbl[30])
            copy_resource(self.p_context, self.p_staging_tex, p_desktop_tex)

            _release_com_ptr(p_desktop_tex)
            _release_com_ptr(p_resource)
            release_frame_func(self.p_duplication)

            map_func = WINFUNCTYPE(
                c_long, c_void_p, c_void_p, c_uint, c_uint, c_uint, POINTER(D3D11_MAPPED_SUBRESOURCE)
            )(ctx_vtbl[13])
            unmap_func = WINFUNCTYPE(None, c_void_p, c_void_p, c_uint)(ctx_vtbl[14])

            mapped = D3D11_MAPPED_SUBRESOURCE()
            hr = map_func(self.p_context, self.p_staging_tex, 0, 1, 0, byref(mapped))
            if hr != 0 or not mapped.pData:
                return self.last_frame

            pitch = int(mapped.RowPitch)
            buf_len = pitch * self.height

            ubuf = (c_ubyte * buf_len).from_address(mapped.pData)
            raw_arr = np.frombuffer(ubuf, dtype=np.uint8).reshape((self.height, pitch))

            expected_line = self.width * 4
            if pitch == expected_line:
                frame_bgra = raw_arr.reshape((self.height, self.width, 4))
            else:
                frame_bgra = raw_arr[:, :expected_line].reshape((self.height, self.width, 4))

            frame_bgr = cv2.cvtColor(frame_bgra, cv2.COLOR_BGRA2BGR)
            unmap_func(self.p_context, self.p_staging_tex, 0)

            if not self.first_frame_logged:
                print(f"[DXGI-Perf] First hardware GPU frame captured successfully ({self.width}x{self.height})", flush=True)
                self.first_frame_logged = True

            self.last_frame = frame_bgr
            return frame_bgr

        except Exception:
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


class BITMAPINFOHEADER(Structure):
    _fields_ = [
        ("biSize", c_uint),
        ("biWidth", c_long),
        ("biHeight", c_long),
        ("biPlanes", c_ushort),
        ("biBitCount", c_ushort),
        ("biCompression", c_uint),
        ("biSizeImage", c_uint),
        ("biXPelsPerMeter", c_long),
        ("biYPelsPerMeter", c_long),
        ("biClrUsed", c_uint),
        ("biClrImportant", c_uint),
    ]


class WindowsFastGDIGrabber:
    """Persistent Device Context & Reusable DIB Section Grabber."""

    def __init__(self, mon_left: int = 0, mon_top: int = 0, width: int = 1920, height: int = 1080):
        self.mon_left = mon_left
        self.mon_top = mon_top
        self.width = max(1, width)
        self.height = max(1, height)
        self.src_dc = None
        self.mem_dc = None
        self.h_bitmap = None
        self.p_bits = c_void_p()
        self.initialized = False

        if sys.platform == "win32":
            self.initialized = self._setup()

    def _setup(self) -> bool:
        try:
            user32 = ctypes.windll.user32
            gdi32 = ctypes.windll.gdi32

            self.src_dc = user32.GetDC(0)
            if not self.src_dc:
                return False

            self.mem_dc = gdi32.CreateCompatibleDC(self.src_dc)
            if not self.mem_dc:
                return False

            bih = BITMAPINFOHEADER()
            bih.biSize = sizeof(BITMAPINFOHEADER)
            bih.biWidth = self.width
            bih.biHeight = -self.height
            bih.biPlanes = 1
            bih.biBitCount = 32
            bih.biCompression = 0

            self.h_bitmap = gdi32.CreateDIBSection(
                self.src_dc, byref(bih), 0, byref(self.p_bits), None, 0
            )
            if not self.h_bitmap or not self.p_bits.value:
                return False

            gdi32.SelectObject(self.mem_dc, self.h_bitmap)
            return True
        except Exception:
            return False

    def grab(self) -> Optional[np.ndarray]:
        if not self.initialized:
            return None
        try:
            gdi32 = ctypes.windll.gdi32
            SRCCOPY = 0x00CC0020
            success = gdi32.BitBlt(
                self.mem_dc, 0, 0, self.width, self.height, self.src_dc, self.mon_left, self.mon_top, SRCCOPY
            )
            if not success:
                return None

            buf_size = self.width * self.height * 4
            c_buf = (c_ubyte * buf_size).from_address(self.p_bits.value)
            raw_4ch = np.frombuffer(c_buf, dtype=np.uint8).reshape((self.height, self.width, 4))
            return cv2.cvtColor(raw_4ch, cv2.COLOR_BGRA2BGR)
        except Exception:
            return None

    def close(self):
        if sys.platform == "win32":
            try:
                gdi32 = ctypes.windll.gdi32
                user32 = ctypes.windll.user32
                if self.h_bitmap:
                    gdi32.DeleteObject(self.h_bitmap)
                if self.mem_dc:
                    gdi32.DeleteDC(self.mem_dc)
                if self.src_dc:
                    user32.ReleaseDC(0, self.src_dc)
            except Exception:
                pass
        self.initialized = False


class POINT(Structure):
    _fields_ = [("x", c_long), ("y", c_long)]


def get_system_cursor_position() -> tuple[float, float]:
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

        cv2.polylines(
            bgr_image,
            [pts],
            isClosed=True,
            color=col_black,
            thickness=max(2, int(round(2 * size_mult))),
            lineType=cv2.LINE_AA,
        )
        cv2.fillPoly(
            bgr_image,
            [pts],
            color=col_white,
            lineType=cv2.LINE_AA,
        )
        cv2.polylines(
            bgr_image,
            [pts],
            isClosed=True,
            color=col_dark,
            thickness=1,
            lineType=cv2.LINE_AA,
        )