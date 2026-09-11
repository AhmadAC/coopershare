"""
Cross-Platform Universal Input Injector supporting native Win32 API (Windows),
evdev direct touchscreen and pointer injection (Linux Wayland / X11), and pynput fallback.
"""

import ctypes
import os
import sys
import numpy as np

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
    MOUSEEVENTF_LEFTDOWN = 0x0002
    MOUSEEVENTF_LEFTUP = 0x0004
    MOUSEEVENTF_RIGHTDOWN = 0x0008
    MOUSEEVENTF_RIGHTUP = 0x0010
    MOUSEEVENTF_MIDDLEDOWN = 0x0020
    MOUSEEVENTF_MIDDLEUP = 0x0040
    MOUSEEVENTF_WHEEL = 0x0800

    def __init__(self, screen_w: int, screen_h: int, mon_left: int = 0, mon_top: int = 0):
        self.screen_w = max(1, screen_w)
        self.screen_h = max(1, screen_h)
        self.mon_left = mon_left
        self.mon_top = mon_top
        self.mode = "none"
        self.mouse = None
        self.ui = None

        if sys.platform == "win32":
            self.mode = "win32"
            print(f"[DEBUG Injector] Using native Win32 hardware input injection on bounds ({mon_left},{mon_top},{screen_w}x{screen_h}).")
        elif USE_EVDEV:
            try:
                min_x = min(0, mon_left)
                max_x = max(self.screen_w, mon_left + self.screen_w)
                min_y = min(0, mon_top)
                max_y = max(self.screen_h, mon_top + self.screen_h)

                cap = {
                    e.EV_KEY: [
                        e.BTN_LEFT,
                        e.BTN_RIGHT,
                        e.BTN_MIDDLE,
                        e.BTN_TOUCH,
                        e.BTN_TOOL_FINGER,
                    ],
                    e.EV_ABS: [
                        (e.ABS_X, AbsInfo(value=0, min=min_x, max=max_x, fuzz=0, flat=0, resolution=1)),
                        (e.ABS_Y, AbsInfo(value=0, min=min_y, max=max_y, fuzz=0, flat=0, resolution=1)),
                    ],
                    e.EV_REL: [e.REL_WHEEL],
                }

                self.ui = UInput(cap, name="mrcoopers-virtual-input")
                self.mode = "evdev"
                print(f"[DEBUG Injector] Linux evdev kernel virtual pointer active ({max_x}x{max_y}).")
            except PermissionError as p_err:
                print(
                    f"\n[ERROR Injector] Permission denied on /dev/uinput: {p_err}\n"
                    "Run: sudo setfacl -m u:$USER:rw /dev/uinput\n"
                )
                self.mode = "none"
            except Exception as ex:
                print(f"[DEBUG Injector] evdev init failed: {ex}")
                self.mode = "none"

        if self.mode == "none":
            if MouseController is not None:
                try:
                    self.mouse = MouseController()
                    self.mode = "pynput"
                    print("[DEBUG Injector] Using pynput mouse controller fallback.")
                except Exception as ex:
                    print(f"[DEBUG Injector] pynput init failed: {ex}")
                    self.mode = "unsupported"
            else:
                self.mode = "unsupported"

    def execute(self, event: dict):
        ev_type = event.get("type")
        nx = event.get("x")
        ny = event.get("y")

        if nx is not None and ny is not None:
            px = self.mon_left + int(np.clip(nx, 0.0, 1.0) * (self.screen_w - 1))
            py = self.mon_top + int(np.clip(ny, 0.0, 1.0) * (self.screen_h - 1))
        else:
            px, py = None, None

        if self.mode == "win32":
            try:
                if px is not None and py is not None:
                    ctypes.windll.user32.SetCursorPos(int(px), int(py))
                if ev_type in ("touch_down", "mouse_down"):
                    btn = event.get("button", "left")
                    if btn == "right":
                        ctypes.windll.user32.mouse_event(self.MOUSEEVENTF_RIGHTDOWN, 0, 0, 0, 0)
                    elif btn == "middle":
                        ctypes.windll.user32.mouse_event(self.MOUSEEVENTF_MIDDLEDOWN, 0, 0, 0, 0)
                    else:
                        ctypes.windll.user32.mouse_event(self.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
                elif ev_type in ("touch_up", "mouse_up"):
                    btn = event.get("button", "left")
                    if btn == "right":
                        ctypes.windll.user32.mouse_event(self.MOUSEEVENTF_RIGHTUP, 0, 0, 0, 0)
                    elif btn == "middle":
                        ctypes.windll.user32.mouse_event(self.MOUSEEVENTF_MIDDLEUP, 0, 0, 0, 0)
                    else:
                        ctypes.windll.user32.mouse_event(self.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
                elif ev_type == "scroll":
                    dy = event.get("dy", 0)
                    delta = 120 if dy > 0 else -120
                    ctypes.windll.user32.mouse_event(self.MOUSEEVENTF_WHEEL, 0, 0, delta, 0)
            except Exception as ex:
                print(f"[DEBUG Injector Win32] Execution failed: {ex}")

        elif self.mode == "evdev" and self.ui:
            try:
                if px is not None and py is not None:
                    self.ui.write(e.EV_ABS, e.ABS_X, int(px))
                    self.ui.write(e.EV_ABS, e.ABS_Y, int(py))

                if ev_type in ("touch_down", "mouse_down"):
                    btn_type = event.get("button", "left")
                    btn_code = (
                        e.BTN_RIGHT
                        if btn_type == "right"
                        else (e.BTN_MIDDLE if btn_type == "middle" else e.BTN_LEFT)
                    )
                    if btn_code == e.BTN_LEFT:
                        self.ui.write(e.EV_KEY, e.BTN_TOUCH, 1)
                    self.ui.write(e.EV_KEY, btn_code, 1)
                    self.ui.syn()
                    print(f"[DEBUG Injector evdev] Touch Down at ({px}, {py}) btn={btn_type}")

                elif ev_type in ("touch_up", "mouse_up"):
                    btn_type = event.get("button", "left")
                    btn_code = (
                        e.BTN_RIGHT
                        if btn_type == "right"
                        else (e.BTN_MIDDLE if btn_type == "middle" else e.BTN_LEFT)
                    )
                    if btn_code == e.BTN_LEFT:
                        self.ui.write(e.EV_KEY, e.BTN_TOUCH, 0)
                    self.ui.write(e.EV_KEY, btn_code, 0)
                    self.ui.syn()
                    print(f"[DEBUG Injector evdev] Touch Up at ({px}, {py}) btn={btn_type}")

                elif ev_type in ("touch_move", "mouse_move"):
                    self.ui.syn()

                elif ev_type == "scroll":
                    dy = 1 if event.get("dy", 0) > 0 else -1
                    self.ui.write(e.EV_REL, e.REL_WHEEL, dy)
                    self.ui.syn()
            except Exception as ex:
                print(f"[DEBUG Injector evdev] Execution failed: {ex}")

        elif self.mode == "pynput" and self.mouse:
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

    def close(self):
        if self.mode == "evdev" and self.ui:
            try:
                self.ui.close()
            except Exception:
                pass