"""
Native 64-bit Windows WASAPI Desktop Audio Loopback & Physical Speaker Mute Controller (ctypes COM).
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
    c_short,
    c_ubyte,
    c_uint,
    c_uint64,
    c_ulong,
    c_ushort,
    c_void_p,
)
import sys
from typing import Optional

import numpy as np

from config import CHANNELS, DEFAULT_SAMPLE_RATE


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

            hr = activate(
                p_dev, byref(IID_IAudioEndpointVolume), CLSCTX_ALL, None, byref(p_ep_vol)
            )
            if hr != 0 or not p_ep_vol.value:
                return False

            ep_vtbl = ctypes.cast(p_ep_vol, POINTER(POINTER(c_void_p))).contents
            set_mute = ctypes.WINFUNCTYPE(HRESULT, c_void_p, c_int, c_void_p)(
                ep_vtbl[14]
            )
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

            hr = activate(
                p_dev, byref(IID_IAudioEndpointVolume), CLSCTX_ALL, None, byref(p_ep_vol)
            )
            if hr != 0 or not p_ep_vol.value:
                return False

            ep_vtbl = ctypes.cast(p_ep_vol, POINTER(POINTER(c_void_p))).contents
            get_mute = ctypes.WINFUNCTYPE(HRESULT, c_void_p, POINTER(c_int))(
                ep_vtbl[15]
            )
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
                self.p_device,
                byref(IID_IAudioClient),
                CLSCTX_ALL,
                None,
                byref(self.audio_client),
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
                self.audio_client,
                byref(IID_IAudioCaptureClient),
                byref(self.capture_client),
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
                        scaled = np.clip(
                            arr.astype(np.float32) * volume, -32768.0, 32767.0
                        ).astype(np.int16)
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