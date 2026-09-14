# threads.py

"""
Background network worker threads for video, audio, input, reverse video, and beacon discovery.
Supports native Windows WASAPI loopback, KDE Plasma 6 KWin D-Bus ScreenShot2 kernel pipe capture,
pipelined asynchronous streaming, KDE Spectacle fallback, and MSS hardware capture.
"""

try:
    import fcntl
except (ImportError, ModuleNotFoundError):
    fcntl = None

import json
import os
import queue
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
from typing import Optional

import cv2
import numpy as np
from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QGuiApplication, QImage

try:
    from PySide6.QtDBus import QDBusMessage
except (ImportError, ModuleNotFoundError):
    QDBusMessage = None

from audio_backend import NativeWindowsWasapiLoopback
from config import (
    AUDIO_AVAILABLE,
    AUDIO_PORT,
    CHANNELS,
    CONTROL_PORT,
    DEFAULT_SAMPLE_RATE,
    DISCOVERY_PORT,
    REVERSE_VIDEO_PORT,
    VIDEO_PORT,
)
from input_backend import UniversalInputInjector
from utils import create_mss_instance, ensure_kde_desktop_entry, recv_exact
from video_backend import render_cursor_on_frame

if AUDIO_AVAILABLE:
    import sounddevice as sd


def _is_dbus_error(msg) -> bool:
    """Safely determines if a QDBusMessage is an error across all Qt/PySide versions."""
    if msg is None or QDBusMessage is None:
        return True
    try:
        err_msg = msg.errorMessage()
        if err_msg:
            return True
    except Exception:
        pass

    try:
        if hasattr(QDBusMessage, "ErrorMessage") and msg.type() == QDBusMessage.ErrorMessage:
            return True
        if (
            hasattr(QDBusMessage, "MessageType")
            and msg.type() == QDBusMessage.MessageType.ErrorMessage
        ):
            return True
    except Exception:
        pass
    return False


def _extract_dict_from_dbus_meta(meta_raw) -> dict:
    """Safely unpacks PySide6 QDBusArgument metadata into a Python dictionary."""
    if isinstance(meta_raw, dict):
        return meta_raw
    for method_name in ("asVariant", "toVariant"):
        if hasattr(meta_raw, method_name):
            try:
                val = getattr(meta_raw, method_name)()
                if isinstance(val, dict):
                    return val
            except Exception:
                pass
    return {}


class FastPipeReader:
    """High-speed zero-copy triple-buffered worker that reads exact frame payloads from Linux kernel pipe."""

    def __init__(self, initial_capacity: int = 1920 * 1080 * 4 + 131072):
        self.buffers = [
            bytearray(initial_capacity),
            bytearray(initial_capacity),
            bytearray(initial_capacity),
        ]
        self.buf_idx = 0
        self.bytes_read = 0
        self.expected_size = 0
        self.fd = -1
        self.start_evt = threading.Event()
        self.done_evt = threading.Event()
        self.running = True
        self.worker = threading.Thread(target=self._run, name="FastPipeWorker", daemon=True)
        self.worker.start()

    def start_read(self, fd: int, expected_size: int = 0):
        self.fd = fd
        self.expected_size = expected_size
        self.bytes_read = 0
        self.buf_idx = (self.buf_idx + 1) % len(self.buffers)
        self.done_evt.clear()
        self.start_evt.set()

    def wait_read(self, timeout: float = 0.5) -> Optional[memoryview]:
        signaled = self.done_evt.wait(timeout)
        if not signaled or self.bytes_read <= 0:
            return None
        buf = self.buffers[self.buf_idx]
        return memoryview(buf)[: self.bytes_read]

    def _run(self):
        while self.running:
            self.start_evt.wait()
            self.start_evt.clear()
            if not self.running:
                break

            fd = self.fd
            expected = self.expected_size
            buf = self.buffers[self.buf_idx]
            if expected > len(buf):
                buf.extend(b"\x00" * (expected - len(buf) + 131072))
            mv = memoryview(buf)
            offset = 0

            try:
                while expected <= 0 or offset < expected:
                    chunk_size = min(1048576, expected - offset) if expected > 0 else 1048576
                    chunk = os.read(fd, chunk_size)
                    if not chunk:
                        break
                    mv[offset : offset + len(chunk)] = chunk
                    offset += len(chunk)
            except Exception:
                pass

            self.bytes_read = offset
            self.done_evt.set()

    def stop(self):
        self.running = False
        self.start_evt.set()


class KWinScreenShot2Grabber:
    """Zero-overhead native KDE Plasma 6 (Wayland) KWin ScreenShot2 kernel pipe capture."""

    def __init__(self):
        self.available = False
        self.iface = None
        self.native_w = 1920
        self.native_h = 1080
        self.logical_w = 1536
        self.logical_h = 864
        self.dpr = 1.0
        self.pipe_reader = None

        if not sys.platform.startswith("linux") or fcntl is None:
            return

        screen = QGuiApplication.primaryScreen()
        if screen:
            self.dpr = float(screen.devicePixelRatio())
            self.logical_w = screen.size().width()
            self.logical_h = screen.size().height()
            self.native_w = int(round(self.logical_w * self.dpr))
            self.native_h = int(round(self.logical_h * self.dpr))

        self.pipe_reader = FastPipeReader()

        try:
            from PySide6.QtDBus import QDBusConnection, QDBusInterface

            def _get_interface():
                return QDBusInterface(
                    "org.kde.KWin.ScreenShot2",
                    "/org/kde/KWin/ScreenShot2",
                    "org.kde.KWin.ScreenShot2",
                    QDBusConnection.sessionBus(),
                )

            self.iface = _get_interface()
            if self.iface.isValid():
                for attempt in range(2):
                    test_frame = self.grab(include_cursor=True, native_resolution=False, init_timeout=1.5)
                    if test_frame is not None and test_frame.size > 0:
                        self.available = True
                        break
                    else:
                        if attempt == 0:
                            ensure_kde_desktop_entry(force=True)
                            time.sleep(0.3)
                            self.iface = _get_interface()
        except Exception:
            self.available = False

    def grab(
        self,
        include_cursor: bool = True,
        native_resolution: bool = False,
        init_timeout: float = 0.25,
    ) -> Optional[np.ndarray]:
        if not self.iface or not self.pipe_reader or fcntl is None:
            return None
        r_fd, w_fd = -1, -1
        try:
            from PySide6.QtDBus import QDBusUnixFileDescriptor

            r_fd, w_fd = os.pipe()

            try:
                fcntl.fcntl(r_fd, 1031, 1048576)
            except Exception:
                pass

            q_fd = QDBusUnixFileDescriptor(w_fd)

            options = {}
            if include_cursor:
                options["include-cursor"] = True
            if native_resolution:
                options["native-resolution"] = True

            target_w = self.native_w if native_resolution else self.logical_w
            target_h = self.native_h if native_resolution else self.logical_h
            expected_bytes = target_w * target_h * 4

            self.pipe_reader.start_read(r_fd, expected_bytes)

            reply = self.iface.call("CaptureActiveScreen", options, q_fd)
            os.close(w_fd)
            w_fd = -1
            q_fd = None

            if _is_dbus_error(reply) or not reply.arguments():
                r_fd2, w_fd2 = os.pipe()
                try:
                    fcntl.fcntl(r_fd2, 1031, 1048576)
                except Exception:
                    pass
                q_fd2 = QDBusUnixFileDescriptor(w_fd2)

                self.pipe_reader.start_read(r_fd2, expected_bytes)
                reply2 = self.iface.call("CaptureWorkspace", options, q_fd2)
                os.close(w_fd2)
                w_fd2 = -1
                q_fd2 = None
                os.close(r_fd)
                r_fd = r_fd2

                if _is_dbus_error(reply2) or not reply2.arguments():
                    os.close(r_fd)
                    r_fd = -1
                    return None
                meta_raw = reply2.arguments()[0]
            else:
                meta_raw = reply.arguments()[0]

            raw_mv = self.pipe_reader.wait_read(timeout=init_timeout)
            os.close(r_fd)
            r_fd = -1

            if not raw_mv:
                return None

            total_pixels = len(raw_mv) // 4
            if total_pixels <= 0:
                return None

            meta = _extract_dict_from_dbus_meta(meta_raw)
            mw = int(meta.get("width", 0))
            mh = int(meta.get("height", 0))

            if mw > 0 and mh > 0 and (mw * mh == total_pixels):
                width, height = mw, mh
            elif target_w * target_h == total_pixels:
                width, height = target_w, target_h
            elif self.native_w * self.native_h == total_pixels:
                width, height = self.native_w, self.native_h
            elif self.logical_w * self.logical_h == total_pixels:
                width, height = self.logical_w, self.logical_h
            else:
                aspect = self.logical_w / self.logical_h if self.logical_h > 0 else (16.0 / 9.0)
                height = int(round((total_pixels / aspect) ** 0.5))
                width = int(round(height * aspect))

            stride = width * 4
            total_expected_bytes = stride * height
            if len(raw_mv) < total_expected_bytes:
                return None

            arr = np.frombuffer(raw_mv, dtype=np.uint8, count=total_expected_bytes).reshape((height, width, 4))
            return arr

        except Exception:
            if w_fd != -1:
                try:
                    os.close(w_fd)
                except Exception:
                    pass
            if r_fd != -1:
                try:
                    os.close(r_fd)
                except Exception:
                    pass
            return None

    def cleanup(self):
        if self.pipe_reader:
            self.pipe_reader.stop()


class SpectacleGrabber:
    """Fallback screencopy using KDE Spectacle CLI with shared-memory pipe."""

    def __init__(self):
        self.available = False
        self.bin_path = shutil.which("spectacle")
        shm_dir = "/dev/shm" if os.path.isdir("/dev/shm") and os.access("/dev/shm", os.W_OK) else "/tmp"
        self.temp_file = os.path.join(shm_dir, f"coopershare_spectacle_{os.getpid()}.jpg")

        if self.bin_path:
            try:
                cmd = [self.bin_path, "-b", "-n", "-f", "-o", self.temp_file]
                r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=4.0)
                if r.returncode == 0 and os.path.exists(self.temp_file) and os.path.getsize(self.temp_file) > 100:
                    self.available = True
            except Exception:
                pass
            finally:
                if os.path.exists(self.temp_file):
                    try:
                        os.remove(self.temp_file)
                    except Exception:
                        pass

    def grab(self) -> Optional[np.ndarray]:
        if not self.available or not self.bin_path:
            return None
        try:
            cmd = [self.bin_path, "-b", "-n", "-f", "-o", self.temp_file]
            r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3.0)
            if r.returncode == 0 and os.path.exists(self.temp_file):
                bgr = cv2.imread(self.temp_file)
                try:
                    os.remove(self.temp_file)
                except Exception:
                    pass
                return bgr
        except Exception:
            pass
        return None

    def cleanup(self):
        if os.path.exists(self.temp_file):
            try:
                os.remove(self.temp_file)
            except Exception:
                pass


def probe_grim(grim_bin: str) -> tuple[bool, list[str]]:
    """Probes grim for supported formats."""
    try:
        r = subprocess.run(
            [grim_bin, "-t", "jpeg", "-q", "75", "-"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=0.8,
        )
        if r.returncode == 0 and len(r.stdout) > 100:
            return True, ["-t", "jpeg"]
    except Exception:
        pass

    try:
        r = subprocess.run(
            [grim_bin, "-t", "ppm", "-"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=0.8,
        )
        if r.returncode == 0 and len(r.stdout) > 100:
            return True, ["-t", "ppm"]
    except Exception:
        pass

    return False, []


class DiscoveryListenerThread(QThread):
    device_found = Signal(str, bool)

    def __init__(self):
        super().__init__()
        self.running = True

    def run(self):
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
        except Exception:
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
            except Exception:
                continue

        sock.close()

    def stop(self):
        self.running = False
        self.wait(1000)


class ScreenSenderThread(QThread):
    status_changed = Signal(str, bool)
    fps_updated = Signal(float)

    def __init__(
        self,
        target_ip: str,
        pin: str = "",
        quality: int = 89,
        fps_limit: int = 60,
        use_444_chroma: bool = False,
        native_resolution: bool = False,
    ):
        super().__init__()
        self.target_ip = target_ip
        self.pin = pin
        self.quality = quality
        self.fps_limit = fps_limit
        self.use_444_chroma = use_444_chroma
        self.native_resolution = native_resolution
        self.running = True
        self.paused = False
        self._pause_requested = False

        # 3-Stage Concurrent Pipeline Queues (Capture -> Encode -> Network Send)
        self.raw_queue = queue.Queue(maxsize=1)
        self.send_queue = queue.Queue(maxsize=1)
        self.pipeline_running = False

        self._stats_lock = threading.Lock()
        self._frames_sent = 0
        self._last_net_duration = 0.0

    def set_fps_limit(self, fps: int):
        self.fps_limit = max(1, fps)

    def set_quality_params(self, quality: int, use_444: bool, native_resolution: bool = False):
        self.quality = quality
        self.use_444_chroma = use_444
        self.native_resolution = native_resolution

    def pause_stream(self):
        """Signals the capture pipeline to hide cursor, capture a clean frame, send it, and pause."""
        self._pause_requested = True

    def resume_stream(self):
        """Resumes streaming and restores mouse cursor capture."""
        self._pause_requested = False
        self.paused = False

    def trigger_cursorless_frame(self):
        self.pause_stream()

    def _encoder_worker(self):
        """Stage 2: High-speed concurrent worker for parallel SIMD JPEG encoding."""
        while self.pipeline_running:
            try:
                item = self.raw_queue.get(timeout=0.04)
            except queue.Empty:
                continue

            frame_raw, t_cap_ms = item

            eff_quality = self.quality
            if self._last_net_duration > 22.0:
                eff_quality = max(70, self.quality - 4)

            encode_params = [int(cv2.IMWRITE_JPEG_QUALITY), eff_quality]

            if self.use_444_chroma:
                sampling_factor_id = getattr(cv2, "IMWRITE_JPEG_SAMPLING_FACTOR", 10)
                sampling_444_val = getattr(cv2, "IMWRITE_JPEG_SAMPLING_FACTOR_444", 0x00010001)
                encode_params.extend([int(sampling_factor_id), int(sampling_444_val)])

            try:
                success, enc_img = cv2.imencode(".jpg", frame_raw, encode_params)
            except Exception:
                if frame_raw.ndim == 3 and frame_raw.shape[2] == 4:
                    frame_bgr = cv2.cvtColor(frame_raw, cv2.COLOR_BGRA2BGR)
                else:
                    frame_bgr = frame_raw
                success, enc_img = cv2.imencode(".jpg", frame_bgr, encode_params)

            if success and enc_img is not None and self.pipeline_running:
                data = enc_img.tobytes()
                if self.send_queue.full():
                    try:
                        self.send_queue.get_nowait()
                    except queue.Empty:
                        pass
                self.send_queue.put_nowait(data)

    def _network_sender_worker(self, sock: socket.socket):
        """Stage 3: Dedicated concurrent worker for socket transmission decoupled from encoder."""
        last_report_time = time.perf_counter()

        while self.pipeline_running:
            try:
                data = self.send_queue.get(timeout=0.04)
            except queue.Empty:
                continue

            t_send_start = time.perf_counter()
            try:
                sock.sendall(struct.pack(">L", len(data)) + data)
            except Exception:
                self.pipeline_running = False
                break
            t_send_end = time.perf_counter()
            self._last_net_duration = (t_send_end - t_send_start) * 1000.0

            with self._stats_lock:
                self._frames_sent += 1

            now = time.perf_counter()
            if now - last_report_time >= 1.0:
                elapsed = now - last_report_time
                with self._stats_lock:
                    count = self._frames_sent
                    self._frames_sent = 0

                measured_fps = count / elapsed if elapsed > 0 else 0.0
                self.fps_updated.emit(measured_fps)
                last_report_time = now

    def run(self):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4 * 1024 * 1024)
            except Exception:
                pass

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

            if not resp.get("auth", False):
                err_msg = resp.get("msg", "Auth Failed")
                self.status_changed.emit(f"Error: {err_msg}", False)
                sock.close()
                return

            sock.settimeout(None)
            self.status_changed.emit(f"Streaming ({self.fps_limit} FPS)", True)
        except Exception as e:
            self.status_changed.emit(f"Connect Error: {e}", False)
            return

        is_wayland = sys.platform.startswith("linux") and (
            os.environ.get("XDG_SESSION_TYPE") == "wayland" or os.environ.get("WAYLAND_DISPLAY") is not None
        )

        kwin_grabber = KWinScreenShot2Grabber() if is_wayland else None
        use_kwin = bool(kwin_grabber and kwin_grabber.available)

        spectacle_grabber = None
        use_spectacle = False
        if is_wayland and not use_kwin:
            spectacle_grabber = SpectacleGrabber()
            use_spectacle = spectacle_grabber.available

        self.pipeline_running = True
        enc_worker = threading.Thread(
            target=self._encoder_worker, name="EncoderWorker", daemon=True
        )
        net_worker = threading.Thread(
            target=self._network_sender_worker, args=(sock,), name="NetSenderWorker", daemon=True
        )
        enc_worker.start()
        net_worker.start()

        screen = QGuiApplication.primaryScreen()
        screen_dpr = float(screen.devicePixelRatio()) if screen else 1.0

        with create_mss_instance() as sct:
            monitor = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
            mon_left = monitor.get("left", 0)
            mon_top = monitor.get("top", 0)

            while self.running and self.pipeline_running:
                # Pre-pause execution: hide cursor, grab clean frame, send it, then pause
                if self._pause_requested:
                    use_native_res = bool(self.native_resolution and self.quality >= 95)
                    clean_frame = None
                    try:
                        if use_kwin:
                            clean_frame = kwin_grabber.grab(
                                include_cursor=False,
                                native_resolution=use_native_res,
                            )
                        elif use_spectacle and spectacle_grabber:
                            clean_frame = spectacle_grabber.grab()
                        elif not is_wayland:
                            raw_f = sct.grab(monitor)
                            img_f = np.array(raw_f)
                            clean_frame = cv2.cvtColor(img_f, cv2.COLOR_BGRA2BGR)
                    except Exception:
                        clean_frame = None

                    if clean_frame is not None:
                        # Flush stale queued frames so cursorless frame is delivered immediately
                        while not self.raw_queue.empty():
                            try:
                                self.raw_queue.get_nowait()
                            except queue.Empty:
                                break
                        while not self.send_queue.empty():
                            try:
                                self.send_queue.get_nowait()
                            except queue.Empty:
                                break

                        self.raw_queue.put((clean_frame, 0.0))

                    self.paused = True
                    self._pause_requested = False
                    time.sleep(0.04)
                    continue

                if self.paused:
                    time.sleep(0.04)
                    continue

                t_frame_start = time.perf_counter()

                try:
                    t_cap_start = time.perf_counter()

                    if use_kwin:
                        use_native_res = bool(self.native_resolution and self.quality >= 95)
                        frame_raw = kwin_grabber.grab(
                            include_cursor=True,
                            native_resolution=use_native_res,
                        )
                    elif use_spectacle and spectacle_grabber:
                        frame_raw = spectacle_grabber.grab()
                        if frame_raw is not None:
                            render_cursor_on_frame(
                                frame_raw,
                                monitor_left=mon_left,
                                monitor_top=mon_top,
                                scale_factor=screen_dpr,
                            )
                    elif not is_wayland:
                        raw_frame = sct.grab(monitor)
                        img = np.array(raw_frame)
                        frame_raw = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
                        if frame_raw is not None:
                            render_cursor_on_frame(
                                frame_raw,
                                monitor_left=mon_left,
                                monitor_top=mon_top,
                                scale_factor=screen_dpr,
                            )
                    else:
                        frame_raw = None

                    t_cap_end = time.perf_counter()
                    cap_ms = (t_cap_end - t_cap_start) * 1000.0

                    if frame_raw is None:
                        if is_wayland:
                            time.sleep(0.002)
                        continue

                    if self.raw_queue.full():
                        try:
                            self.raw_queue.get_nowait()
                        except queue.Empty:
                            pass

                    self.raw_queue.put_nowait((frame_raw, cap_ms))

                except (socket.error, BrokenPipeError, ConnectionResetError):
                    break
                except subprocess.TimeoutExpired:
                    time.sleep(0.002)
                    continue
                except Exception:
                    time.sleep(0.002)
                    continue

                frame_elapsed = time.perf_counter() - t_frame_start
                target_frame_time = 1.0 / max(1, self.fps_limit)
                sleep_sec = target_frame_time - frame_elapsed
                if sleep_sec > 0.001:
                    time.sleep(sleep_sec)

        self.pipeline_running = False
        enc_worker.join(timeout=0.4)
        net_worker.join(timeout=0.4)

        if kwin_grabber:
            kwin_grabber.cleanup()
        if spectacle_grabber:
            spectacle_grabber.cleanup()

        try:
            sock.close()
        except Exception:
            pass

        self.status_changed.emit("Disconnected", False)

    def stop(self):
        self.running = False
        self.pipeline_running = False
        self.wait(1000)


class AudioSenderThread(QThread):
    def __init__(self, target_ip: str, volume: float = 1.0):
        super().__init__()
        self.target_ip = target_ip
        self.volume = volume
        self.running = True
        self.muted = False
        self.paused = False
        self.sock: Optional[socket.socket] = None

    def set_volume(self, vol: float):
        self.volume = max(0.0, min(1.5, vol))

    def run(self):
        wasapi = NativeWindowsWasapiLoopback()
        use_native_wasapi = wasapi.start()
        sample_rate = wasapi.sample_rate if use_native_wasapi else DEFAULT_SAMPLE_RATE

        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.sock.settimeout(3.0)
            self.sock.connect((self.target_ip, AUDIO_PORT))

            self.sock.sendall(struct.pack(">I", sample_rate))
            self.sock.settimeout(None)
        except Exception:
            wasapi.stop()
            return

        if use_native_wasapi:
            while self.running:
                eff_vol = 0.0 if (self.muted or self.paused) else self.volume
                chunk = wasapi.read_pcm16_chunk(volume=eff_vol)
                if chunk and self.sock:
                    try:
                        self.sock.sendall(chunk)
                    except Exception:
                        break
                else:
                    self.msleep(4)
            wasapi.stop()
        else:
            if AUDIO_AVAILABLE:

                def callback(indata, frames, time_info, status):
                    if self.running and self.sock:
                        try:
                            if self.muted or self.paused:
                                silence = b"\x00" * (frames * CHANNELS * 2)
                                self.sock.sendall(silence)
                            else:
                                if self.volume != 1.0:
                                    scaled = np.clip(
                                        indata.astype(np.float32) * self.volume,
                                        -32768.0,
                                        32767.0,
                                    ).astype(np.int16)
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
                except Exception:
                    pass

        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass

    def stop(self):
        self.running = False
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
        self.wait(1000)


class InputReceiverThread(QThread):
    def __init__(
        self,
        target_ip: str,
        is_input_enabled_func,
        scr_w: int = 1920,
        scr_h: int = 1080,
        mon_l: int = 0,
        mon_t: int = 0,
    ):
        super().__init__()
        self.target_ip = target_ip
        self.is_input_enabled_func = is_input_enabled_func
        self.scr_w = max(1, scr_w)
        self.scr_h = max(1, scr_h)
        self.mon_l = mon_l
        self.mon_t = mon_t
        self.running = True
        self.sock: Optional[socket.socket] = None
        self._send_lock = threading.Lock()

    def _ensure_socket_connected(self) -> bool:
        with self._send_lock:
            if self.sock:
                return True
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                sock.settimeout(3.0)
                sock.connect((self.target_ip, CONTROL_PORT))
                sock.settimeout(0.5)
                self.sock = sock
                return True
            except Exception:
                return False

    def send_command(self, cmd: dict):
        if not self._ensure_socket_connected():
            return
        with self._send_lock:
            if self.sock:
                try:
                    data = json.dumps(cmd).encode("utf-8")
                    self.sock.sendall(struct.pack(">L", len(data)) + data)
                except Exception:
                    try:
                        self.sock.close()
                    except Exception:
                        pass
                    self.sock = None

    def run(self):
        injector = None
        try:
            injector = UniversalInputInjector(
                self.scr_w, self.scr_h, mon_left=self.mon_l, mon_top=self.mon_t
            )
        except Exception:
            pass

        self._ensure_socket_connected()

        payload_size = struct.calcsize(">L")
        data = bytearray()

        while self.running:
            if not self.sock:
                if not self._ensure_socket_connected():
                    self.msleep(500)
                    continue

            try:
                packet = self.sock.recv(2048)
                if not packet:
                    with self._send_lock:
                        try:
                            self.sock.close()
                        except Exception:
                            pass
                        self.sock = None
                    self.msleep(300)
                    continue
                data.extend(packet)
            except socket.timeout:
                continue
            except (BlockingIOError, InterruptedError):
                continue
            except Exception:
                with self._send_lock:
                    try:
                        if self.sock:
                            self.sock.close()
                    except Exception:
                        pass
                    self.sock = None
                self.msleep(300)
                continue

            while len(data) >= payload_size:
                msg_size = struct.unpack(">L", data[:payload_size])[0]
                if len(data) < payload_size + msg_size:
                    break

                raw_msg = data[payload_size : payload_size + msg_size]
                data = data[payload_size + msg_size :]

                try:
                    event = json.loads(raw_msg.decode("utf-8"))
                    if self.is_input_enabled_func():
                        if injector:
                            injector.execute(event)
                except Exception:
                    pass

        if injector:
            injector.close()
        with self._send_lock:
            try:
                if self.sock:
                    self.sock.close()
            except Exception:
                pass
            self.sock = None

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
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.settimeout(4.0)
            sock.connect((self.target_ip, self.port))
            sock.settimeout(0.5)
        except Exception:
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
            except Exception:
                break

        try:
            sock.close()
        except Exception:
            pass
        self.disconnected.emit()

    def stop(self):
        self.running = False
        self.wait(1000)