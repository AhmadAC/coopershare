#################### START OF FILE: input_backend.py ####################

# input_backend.py

"""
Cross-Platform Universal Input Injector.
Features:
- Ultra-Low Latency Win32 SendInput batch injection (sub-millisecond hardware mouse & keyboard events)
- Direct Linux Kernel /dev/uinput virtual absolute pointer & keyboard driver (Zero external dependencies on Wayland / X11)
- python-evdev driver (if installed)
- pynput fallback (X11 only)
"""

import ctypes
from ctypes import (
    Structure,
    Union,
    byref,
    c_char,
    c_int,
    c_long,
    c_short,
    c_ubyte,
    c_uint,
    c_uint16,
    c_uint32,
    c_ulong,
    c_ushort,
    c_void_p,
    sizeof,
)
import os
import struct
import sys
import time
import numpy as np

try:
    import fcntl
except (ImportError, ModuleNotFoundError):
    fcntl = None

# ---------------------------------------------------------------------------
# Linux Kernel Input Subsystem Constants (linux/input.h & linux/uinput.h)
# ---------------------------------------------------------------------------
EV_SYN = 0x00
EV_KEY = 0x01
EV_REL = 0x02
EV_ABS = 0x03

SYN_REPORT = 0

BTN_LEFT = 0x110
BTN_RIGHT = 0x111
BTN_MIDDLE = 0x112
BTN_SIDE = 0x113
BTN_EXTRA = 0x114

REL_WHEEL = 0x08

ABS_X = 0x00
ABS_Y = 0x01

UI_SET_EVBIT = 0x40045564
UI_SET_KEYBIT = 0x40045565
UI_SET_RELBIT = 0x40045566
UI_SET_ABSBIT = 0x40045567
UI_SET_PROPBIT = 0x4004556E
UI_DEV_CREATE = 0x5501
UI_DEV_DESTROY = 0x5502

INPUT_PROP_POINTER = 0x00
UINPUT_MAX_ABS = 32767


class UInputUserDev(Structure):
    _fields_ = [
        ("name", c_char * 80),
        ("id_bustype", c_uint16),
        ("id_vendor", c_uint16),
        ("id_product", c_uint16),
        ("id_version", c_uint16),
        ("ff_effects_max", c_uint32),
        ("absmax", c_int * 64),
        ("absmin", c_int * 64),
        ("absfuzz", c_int * 64),
        ("absflat", c_int * 64),
    ]


class PurePythonLinuxUInput:
    def __init__(self, max_abs: int = UINPUT_MAX_ABS):
        if fcntl is None:
            raise NotImplementedError("fcntl module is not available on this platform.")

        self.fd = -1
        uinput_path = None
        for cand in ("/dev/uinput", "/dev/input/uinput"):
            if os.path.exists(cand):
                uinput_path = cand
                break

        if not uinput_path:
            raise FileNotFoundError("Linux /dev/uinput device node not found.")

        try:
            self.fd = os.open(uinput_path, os.O_WRONLY | os.O_NONBLOCK)
        except PermissionError:
            raise PermissionError(
                f"Permission denied on {uinput_path}. Run: sudo setfacl -m u:$USER:rw {uinput_path}"
            )

        fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_SYN)
        fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_KEY)
        for btn in (BTN_LEFT, BTN_RIGHT, BTN_MIDDLE, BTN_SIDE, BTN_EXTRA):
            fcntl.ioctl(self.fd, UI_SET_KEYBIT, btn)

        for key_code in range(1, 249):
            try:
                fcntl.ioctl(self.fd, UI_SET_KEYBIT, key_code)
            except Exception:
                pass

        fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_REL)
        fcntl.ioctl(self.fd, UI_SET_RELBIT, REL_WHEEL)

        fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_ABS)
        fcntl.ioctl(self.fd, UI_SET_ABSBIT, ABS_X)
        fcntl.ioctl(self.fd, UI_SET_ABSBIT, ABS_Y)

        try:
            fcntl.ioctl(self.fd, UI_SET_PROPBIT, INPUT_PROP_POINTER)
        except Exception:
            pass

        udev = UInputUserDev()
        udev.name = b"MrCoopersScreenShare-Virtual-Pointer"
        udev.id_bustype = 0x03
        udev.id_vendor = 0x1234
        udev.id_product = 0x5678
        udev.id_version = 1

        udev.absmin[ABS_X] = 0
        udev.absmax[ABS_X] = max_abs
        udev.absmin[ABS_Y] = 0
        udev.absmax[ABS_Y] = max_abs

        os.write(self.fd, bytes(udev))
        fcntl.ioctl(self.fd, UI_DEV_CREATE)
        time.sleep(0.15)

    def write_event(self, ev_type: int, code: int, value: int):
        if self.fd >= 0 and fcntl is not None:
            is_64bit = struct.calcsize("P") == 8
            fmt = "qqHHi" if is_64bit else "iiHHi"
            payload = struct.pack(fmt, 0, 0, ev_type, code, value)
            os.write(self.fd, payload)

    def syn(self):
        self.write_event(EV_SYN, SYN_REPORT, 0)

    def close(self):
        if self.fd >= 0:
            try:
                if fcntl is not None:
                    fcntl.ioctl(self.fd, UI_DEV_DESTROY)
                os.close(self.fd)
            except Exception:
                pass
            self.fd = -1


# ---------------------------------------------------------------------------
# Win32 SendInput Structures (Direct Kernel Hardware Event Queue)
# ---------------------------------------------------------------------------
INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_ABSOLUTE = 0x8000

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004


class MOUSEINPUT(Structure):
    _fields_ = [
        ("dx", c_long),
        ("dy", c_long),
        ("mouseData", c_ulong),
        ("dwFlags", c_ulong),
        ("time", c_ulong),
        ("dwExtraInfo", c_void_p),
    ]


class KEYBDINPUT(Structure):
    _fields_ = [
        ("wVk", c_ushort),
        ("wScan", c_ushort),
        ("dwFlags", c_ulong),
        ("time", c_ulong),
        ("dwExtraInfo", c_void_p),
    ]


class HARDWAREINPUT(Structure):
    _fields_ = [
        ("uMsg", c_ulong),
        ("wParamL", c_short),
        ("wParamH", c_ushort),
    ]


class _INPUT_UNION(Union):
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    ]


class INPUT(Structure):
    _fields_ = [
        ("type", c_ulong),
        ("u", _INPUT_UNION),
    ]


USE_EVDEV = False
if sys.platform.startswith("linux"):
    try:
        import evdev
        from evdev import AbsInfo, UInput, ecodes as e

        USE_EVDEV = True
    except Exception:
        USE_EVDEV = False

Button = None
MouseController = None
try:
    from pynput.mouse import Button, Controller as MouseController
except Exception:
    Button = None
    MouseController = None


class UniversalInputInjector:
    def __init__(self, screen_w: int, screen_h: int, mon_left: int = 0, mon_top: int = 0):
        self.screen_w = max(1, screen_w)
        self.screen_h = max(1, screen_h)
        self.mon_left = mon_left
        self.mon_top = mon_top
        self.mode = "none"
        self.mouse = None
        self.ui = None
        self.native_uinput = None

        if sys.platform == "win32":
            self.mode = "win32"
        elif sys.platform.startswith("linux"):
            if USE_EVDEV:
                try:
                    cap = {
                        e.EV_KEY: [
                            e.BTN_LEFT,
                            e.BTN_RIGHT,
                            e.BTN_MIDDLE,
                            e.BTN_SIDE,
                            e.BTN_EXTRA,
                        ],
                        e.EV_ABS: [
                            (e.ABS_X, AbsInfo(value=0, min=0, max=UINPUT_MAX_ABS, fuzz=0, flat=0, resolution=1)),
                            (e.ABS_Y, AbsInfo(value=0, min=0, max=UINPUT_MAX_ABS, fuzz=0, flat=0, resolution=1)),
                        ],
                        e.EV_REL: [e.REL_WHEEL],
                    }
                    self.ui = UInput(cap, name="mrcoopers-virtual-pointer", input_props=[0])
                    self.mode = "evdev"
                except Exception:
                    pass

            if self.mode == "none" and fcntl is not None:
                try:
                    self.native_uinput = PurePythonLinuxUInput(max_abs=UINPUT_MAX_ABS)
                    self.mode = "direct_uinput"
                except Exception:
                    pass

        if self.mode == "none":
            if MouseController is not None:
                try:
                    self.mouse = MouseController()
                    self.mode = "pynput"
                except Exception:
                    self.mode = "unsupported"
            else:
                self.mode = "unsupported"

    def update_geometry(self, screen_w: int, screen_h: int, mon_left: int = 0, mon_top: int = 0):
        self.screen_w = max(1, screen_w)
        self.screen_h = max(1, screen_h)
        self.mon_left = mon_left
        self.mon_top = mon_top

    def execute(self, event: dict):
        ev_type = event.get("type")
        nx = event.get("x")
        ny = event.get("y")

        if nx is not None and ny is not None:
            px = self.mon_left + int(np.clip(nx, 0.0, 1.0) * (self.screen_w - 1))
            py = self.mon_top + int(np.clip(ny, 0.0, 1.0) * (self.screen_h - 1))
            abs_x = int(np.clip(nx, 0.0, 1.0) * UINPUT_MAX_ABS)
            abs_y = int(np.clip(ny, 0.0, 1.0) * UINPUT_MAX_ABS)
        else:
            px, py, abs_x, abs_y = None, None, None, None

        if self.mode == "win32":
            try:
                if px is not None and py is not None:
                    ctypes.windll.user32.SetCursorPos(int(px), int(py))

                inp = INPUT()
                inp.type = INPUT_MOUSE
                inp.u.mi.time = 0
                inp.u.mi.dwExtraInfo = None

                if ev_type in ("touch_down", "mouse_down"):
                    btn = event.get("button", "left")
                    if btn == "right":
                        inp.u.mi.dwFlags = MOUSEEVENTF_RIGHTDOWN
                    elif btn == "middle":
                        inp.u.mi.dwFlags = MOUSEEVENTF_MIDDLEDOWN
                    else:
                        inp.u.mi.dwFlags = MOUSEEVENTF_LEFTDOWN
                    ctypes.windll.user32.SendInput(1, byref(inp), sizeof(INPUT))

                elif ev_type in ("touch_up", "mouse_up"):
                    btn = event.get("button", "left")
                    if btn == "right":
                        inp.u.mi.dwFlags = MOUSEEVENTF_RIGHTUP
                    elif btn == "middle":
                        inp.u.mi.dwFlags = MOUSEEVENTF_MIDDLEUP
                    else:
                        inp.u.mi.dwFlags = MOUSEEVENTF_LEFTUP
                    ctypes.windll.user32.SendInput(1, byref(inp), sizeof(INPUT))

                elif ev_type in ("touch_move", "mouse_move"):
                    inp.u.mi.dwFlags = MOUSEEVENTF_MOVE
                    ctypes.windll.user32.SendInput(1, byref(inp), sizeof(INPUT))

                elif ev_type == "scroll":
                    dy = event.get("dy", 0)
                    inp.u.mi.dwFlags = MOUSEEVENTF_WHEEL
                    inp.u.mi.mouseData = 120 if dy > 0 else 0xFFFFFF88
                    ctypes.windll.user32.SendInput(1, byref(inp), sizeof(INPUT))

                elif ev_type in ("key_down", "key_up"):
                    vk = event.get("key_code", 0)
                    kinp = INPUT()
                    kinp.type = INPUT_KEYBOARD
                    kinp.u.ki.wVk = vk & 0xFFFF
                    kinp.u.ki.wScan = 0
                    kinp.u.ki.dwFlags = KEYEVENTF_KEYUP if ev_type == "key_up" else 0
                    kinp.u.ki.time = 0
                    kinp.u.ki.dwExtraInfo = None
                    ctypes.windll.user32.SendInput(1, byref(kinp), sizeof(INPUT))
            except Exception:
                pass

        elif self.mode == "direct_uinput" and self.native_uinput:
            try:
                if abs_x is not None and abs_y is not None:
                    self.native_uinput.write_event(EV_ABS, ABS_X, abs_x)
                    self.native_uinput.write_event(EV_ABS, ABS_Y, abs_y)
                    self.native_uinput.syn()

                if ev_type in ("touch_down", "mouse_down"):
                    btn_type = event.get("button", "left")
                    btn_code = BTN_RIGHT if btn_type == "right" else (BTN_MIDDLE if btn_type == "middle" else BTN_LEFT)
                    self.native_uinput.write_event(EV_KEY, btn_code, 1)
                    self.native_uinput.syn()

                elif ev_type in ("touch_up", "mouse_up"):
                    btn_type = event.get("button", "left")
                    btn_code = BTN_RIGHT if btn_type == "right" else (BTN_MIDDLE if btn_type == "middle" else BTN_LEFT)
                    self.native_uinput.write_event(EV_KEY, btn_code, 0)
                    self.native_uinput.syn()

                elif ev_type in ("touch_move", "mouse_move"):
                    self.native_uinput.syn()

                elif ev_type == "scroll":
                    dy = 1 if event.get("dy", 0) > 0 else -1
                    self.native_uinput.write_event(EV_REL, REL_WHEEL, dy)
                    self.native_uinput.syn()
            except Exception:
                pass

        elif self.mode == "evdev" and self.ui:
            try:
                if abs_x is not None and abs_y is not None:
                    self.ui.write(e.EV_ABS, e.ABS_X, abs_x)
                    self.ui.write(e.EV_ABS, e.ABS_Y, abs_y)
                    self.ui.syn()

                if ev_type in ("touch_down", "mouse_down"):
                    btn_type = event.get("button", "left")
                    btn_code = e.BTN_RIGHT if btn_type == "right" else (e.BTN_MIDDLE if btn_type == "middle" else e.BTN_LEFT)
                    self.ui.write(e.EV_KEY, btn_code, 1)
                    self.ui.syn()

                elif ev_type in ("touch_up", "mouse_up"):
                    btn_type = event.get("button", "left")
                    btn_code = e.BTN_RIGHT if btn_type == "right" else (e.BTN_MIDDLE if btn_type == "middle" else e.BTN_LEFT)
                    self.ui.write(e.EV_KEY, btn_code, 0)
                    self.ui.syn()

                elif ev_type in ("touch_move", "mouse_move"):
                    self.ui.syn()

                elif ev_type == "scroll":
                    dy = 1 if event.get("dy", 0) > 0 else -1
                    self.ui.write(e.EV_REL, e.REL_WHEEL, dy)
                    self.ui.syn()
            except Exception:
                pass

        elif self.mode == "pynput" and self.mouse:
            try:
                if px is not None and py is not None:
                    self.mouse.position = (px, py)
                if ev_type in ("touch_down", "mouse_down"):
                    btn = (
                        Button.right
                        if event.get("button") == "right"
                        else (Button.middle if event.get("button") == "middle" else Button.left)
                    )
                    self.mouse.press(btn)
                elif ev_type in ("touch_up", "mouse_up"):
                    btn = (
                        Button.right
                        if event.get("button") == "right"
                        else (Button.middle if event.get("button") == "middle" else Button.left)
                    )
                    self.mouse.release(btn)
                elif ev_type == "scroll":
                    self.mouse.scroll(0, 1 if event.get("dy", 0) > 0 else -1)
            except Exception:
                pass

    def close(self):
        if self.native_uinput:
            self.native_uinput.close()
            self.native_uinput = None

        if self.mode == "evdev" and self.ui:
            try:
                self.ui.close()
            except Exception:
                pass
            self.ui = None