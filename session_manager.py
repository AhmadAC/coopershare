# session_manager.py

import json
import shutil
import socket
import struct
import subprocess
import time
from typing import Optional

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QGuiApplication

from audio_backend import HostAudioController
from threads import (
    AudioSenderThread,
    DiscoveryListenerThread,
    InputReceiverThread,
    ScreenSenderThread,
)
from ui_viewer import RemoteReceiverViewerWindow
from utils import create_mss_instance


class SessionManager(QObject):
    """Central Controller for networking threads, screen capturing, and remote interaction."""
    status_changed = Signal(str, bool, str)  # Text, IsActive, ColorHex
    fps_updated = Signal(float)
    device_discovered = Signal(str, bool)

    def __init__(self):
        super().__init__()
        self.stream_thread: Optional[ScreenSenderThread] = None
        self.audio_thread: Optional[AudioSenderThread] = None
        self.control_thread: Optional[InputReceiverThread] = None
        self.discovery_thread: Optional[DiscoveryListenerThread] = None
        self.viewer_window: Optional[RemoteReceiverViewerWindow] = None
        self.virtual_monitor_proc: Optional[subprocess.Popen] = None

        self.share_extension = False
        self.is_paused = False
        self.is_stream_muted = False
        self.is_host_muted = HostAudioController.get_host_mute()
        self.allow_audio_when_paused = False
        self.tv_volume = 1.0
        self.input_enabled = True

        self.discovery_thread = DiscoveryListenerThread()
        self.discovery_thread.device_found.connect(self.device_discovered.emit)
        self.discovery_thread.start()

    def _get_target_monitor_geometry(self) -> tuple[int, int, int, int]:
        screens = QGuiApplication.screens()
        if screens:
            idx = 1 if (self.share_extension and len(screens) > 1) else 0
            s = screens[idx]
            geom = s.geometry()
            return geom.width(), geom.height(), geom.x(), geom.y()

        with create_mss_instance() as sct:
            num_mons = len(sct.monitors)
            if self.share_extension and num_mons > 2:
                m = sct.monitors[2]
            else:
                m = sct.monitors[1] if num_mons > 1 else sct.monitors[0]
            return m.get("width", 1920), m.get("height", 1080), m.get("left", 0), m.get("top", 0)

    def ensure_control_channel(self, target_ip: str) -> bool:
        if not self.control_thread or not self.control_thread.isRunning():
            scr_w, scr_h, mon_l, mon_t = self._get_target_monitor_geometry()
            self.control_thread = InputReceiverThread(
                target_ip, lambda: self.input_enabled, scr_w, scr_h, mon_l, mon_t
            )
            self.control_thread.start()
            for _ in range(20):
                if self.control_thread.sock:
                    break
                time.sleep(0.05)
        return bool(self.control_thread and self.control_thread.isRunning())

    def start_sharing(self, target_ip: str, fps: int, pin: str, quality: int, use_444: bool, native_res: bool):
        if not target_ip:
            return
        self.stream_thread = ScreenSenderThread(
            target_ip=target_ip, pin=pin, quality=quality, fps_limit=fps,
            use_444_chroma=use_444, native_resolution=native_res, share_extension=self.share_extension
        )
        self.stream_thread.status_changed.connect(self._on_stream_status)
        self.stream_thread.fps_updated.connect(self.fps_updated.emit)
        self.stream_thread.start()

        self.audio_thread = AudioSenderThread(target_ip, volume=self.tv_volume)
        self.update_audio_pause_state()
        self.audio_thread.start()

        self.ensure_control_channel(target_ip)
        self.send_window_command(target_ip, "maximize")

    def stop_sharing(self):
        if self.stream_thread:
            try: self.stream_thread.stop()
            except Exception: pass
            self.stream_thread = None

        if self.audio_thread:
            try: self.audio_thread.stop()
            except Exception: pass
            self.audio_thread = None

        self.is_paused = False
        self.update_audio_pause_state()
        self.close_viewer()

        if self.control_thread:
            try: self.control_thread.stop()
            except Exception: pass
            self.control_thread = None

        self.status_changed.emit("Disconnected", False, "#8f9bb3")

    def toggle_pause(self) -> bool:
        if self.stream_thread:
            self.is_paused = not self.is_paused
            if self.is_paused:
                self.stream_thread.pause_stream()
                self.status_changed.emit("Paused", True, "#f37021")
            else:
                self.stream_thread.resume_stream()
                self.status_changed.emit("Streaming", True, "#00d084")
            self.update_audio_pause_state()
        return self.is_paused

    def toggle_stream_mute(self) -> bool:
        if self.audio_thread:
            self.is_stream_muted = not self.is_stream_muted
            self.audio_thread.muted = self.is_stream_muted
        return self.is_stream_muted

    def toggle_host_mute(self) -> bool:
        new_state = not self.is_host_muted
        if HostAudioController.set_host_mute(new_state):
            self.is_host_muted = new_state
        else:
            self.is_host_muted = HostAudioController.get_host_mute()
        return self.is_host_muted

    def toggle_share_extension(self) -> bool:
        import sys
        screens = QGuiApplication.screens()
        num_screens = len(screens)

        if not self.share_extension and num_screens < 2:
            if sys.platform == "win32":
                try: subprocess.Popen(["DisplaySwitch.exe", "/extend"]); time.sleep(0.4)
                except Exception: pass
            elif sys.platform.startswith("linux"):
                kscreen_doctor = shutil.which("kscreen-doctor")
                enabled_physical = False
                if kscreen_doctor:
                    try:
                        out = subprocess.check_output([kscreen_doctor, "-o"], text=True, timeout=1.5)
                        for line in out.splitlines():
                            if "disabled" in line.lower() and "output:" in line.lower():
                                parts = line.split()
                                if len(parts) >= 3:
                                    subprocess.run([kscreen_doctor, f"output.{parts[2]}.enable"], timeout=2.0)
                                    time.sleep(0.5)
                                    enabled_physical = True
                                    break
                    except Exception: pass

                if not enabled_physical:
                    krfb_vmon = shutil.which("krfb-virtualmonitor")
                    if krfb_vmon:
                        try:
                            self.virtual_monitor_proc = subprocess.Popen(
                                [krfb_vmon, "--name", "Virtual-TV", "--resolution", "1920x1080", "--password", "coopershare", "--port", "5908"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                            )
                            time.sleep(1.0)
                        except Exception: pass

        elif self.share_extension and self.virtual_monitor_proc:
            try:
                self.virtual_monitor_proc.terminate()
                self.virtual_monitor_proc.wait(timeout=1.0)
            except Exception:
                try: self.virtual_monitor_proc.kill()
                except Exception: pass
            self.virtual_monitor_proc = None

        self.share_extension = not self.share_extension
        if self.stream_thread and self.stream_thread.isRunning():
            self.stream_thread.set_share_extension(self.share_extension)
        if self.control_thread and self.control_thread.isRunning():
            w, h, l, t = self._get_target_monitor_geometry()
            self.control_thread.update_geometry(w, h, l, t)
        return self.share_extension

    def update_audio_pause_state(self):
        if self.audio_thread:
            self.audio_thread.paused = self.is_paused and not self.allow_audio_when_paused

    def send_window_command(self, target_ip: str, action: str):
        if target_ip and self.ensure_control_channel(target_ip):
            self.control_thread.send_command({"type": "window_control", "action": action})

    def send_timer(self, target_ip: str, args: str):
        if target_ip and self.ensure_control_channel(target_ip):
            self.control_thread.send_command({"type": "timer", "args": args})

    def open_viewer(self, target_ip: str):
        if not target_ip: return
        self.ensure_control_channel(target_ip)
        self.send_window_command(target_ip, "minimize")

        if self.viewer_window:
            try: self.viewer_window.close()
            except Exception: pass

        def send_remote_cmd(cmd: dict):
            if self.ensure_control_channel(target_ip):
                self.control_thread.send_command(cmd)

        self.viewer_window = RemoteReceiverViewerWindow(target_ip, send_remote_cmd)
        self.viewer_window.viewer_closed.connect(self._on_viewer_closed)
        self.viewer_window.show()

    def close_viewer(self):
        if self.viewer_window:
            try: self.viewer_window.close()
            except Exception: pass
            self.viewer_window = None

    def _on_viewer_closed(self):
        self.viewer_window = None

    def _on_stream_status(self, text: str, active: bool):
        if not active:
            self.stop_sharing()
        else:
            self.status_changed.emit(text, active, "#f37021" if self.is_paused else "#00d084")

    def cleanup(self):
        self.stop_sharing()
        if self.virtual_monitor_proc:
            try: self.virtual_monitor_proc.terminate(); self.virtual_monitor_proc.wait(timeout=1.0)
            except Exception: pass
        if self.discovery_thread:
            self.discovery_thread.stop()