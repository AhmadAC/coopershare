"""
Background network worker threads for video, audio, input, reverse video, and beacon discovery.
Supports native Windows WASAPI loopback, KDE Plasma 6 KWin D-Bus ScreenShot2 kernel pipe capture,
pipelined parallel multi-worker streaming, KDE Spectacle fallback, and MSS hardware capture.
"""

import fcntl
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
from PySide6.QtDBus import QDBusMessage
from PySide6.QtGui import QGuiApplication, QImage

from audio_backend import NativeWindowsWasapiLoopback
from config import (
    AUDIO_AVAILABLE,
    AUDIO_PORT,
    CHANNELS,
    CONTROL_PORT,
    DEFAULT_SAMPLE_RATE,
    DISCOVERY_PORT,
    REVERSE_VIDEO_PORT,
    SOCKET_BUFFER_SIZE,
    VIDEO_PORT,
)
from input_backend import UniversalInputInjector
from utils import create_mss_instance, ensure_kde_desktop_entry, recv_exact
from video_backend import render_cursor_on_frame

if AUDIO_AVAILABLE:
    import sounddevice as sd

# Optional PyTurboJPEG SIMD hardware accelerator
try:
    from turbojpeg import TJPF_BGR, TJPF_BGRX, TJSAMP_420, TurboJPEG

    _TURBO_JPEG = TurboJPEG()
    print("[DEBUG Sender Video] PyTurboJPEG SIMD hardware acceleration detected and active.")
except Exception:
    _TURBO_JPEG = None


def _is_dbus_error(msg: QDBusMessage) -> bool:
    """Safely determines if a QDBusMessage is an error across all Qt/PySide versions."""
    if not msg:
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
    """High-speed zero-copy double-buffered worker that reads exact frame payloads from Linux kernel pipe."""

    def __init__(self, initial_capacity: int = 1920 * 1080 * 4 + 131072):
        self.buffers = [bytearray(initial_capacity), bytearray(initial_capacity)]
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
        self.buf_idx = 1 - self.buf_idx
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
                with open(fd, "rb", buffering=0, closefd=False) as f:
                    while expected <= 0 or offset < expected:
                        chunk_size = min(1048576, expected - offset) if expected > 0 else 1048576
                        n = f.readinto(mv[offset : offset + chunk_size])
                        if not n:
                            break
                        offset += n
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
        self.pipe_reader = FastPipeReader()

        screen = QGuiApplication.primaryScreen()
        if screen:
            self.dpr = float(screen.devicePixelRatio())
            self.logical_w = screen.size().width()
            self.logical_h = screen.size().height()
            self.native_w = int(round(self.logical_w * self.dpr))
            self.native_h = int(round(self.logical_h * self.dpr))

        if not sys.platform.startswith("linux"):
            return

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
                    test_frame = self.grab(include_cursor=False, native_resolution=True, init_timeout=1.5)
                    if test_frame is not None and test_frame.size > 0:
                        self.available = True
                        print(
                            f"[DEBUG Screen Capturer] Native KDE Plasma 6 KWin screencopy active ({test_frame.shape[1]}x{test_frame.shape[0]})."
                        )
                        break
                    else:
                        if attempt == 0:
                            ensure_kde_desktop_entry(force=True)
                            time.sleep(0.3)
                            self.iface = _get_interface()
            else:
                print(
                    f"[DEBUG Screen Capturer] KWin ScreenShot2 interface unavailable: {self.iface.lastError().message()}"
                )
        except Exception as e:
            print(f"[DEBUG Screen Capturer] KWin ScreenShot2 probe error: {e}")
            self.available = False

    def grab(
        self,
        include_cursor: bool = False,
        native_resolution: bool = True,
        init_timeout: float = 0.25,
    ) -> Optional[np.ndarray]:
        if not self.iface:
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

            options = {
                "include-cursor": include_cursor,
                "native-resolution": native_resolution,
            }

            target_w = self.native_w if native_resolution else self.logical_w
            target_h = self.native_h if native_resolution else self.logical_h
            expected_bytes = target_w * target_h * 4

            self.pipe_reader.start_read(r_fd, expected_bytes)

            reply = self.iface.call("CaptureActiveScreen", options, q_fd)
            os.close(w_fd)
            w_fd = -1
            q_fd = None

            if _is_dbus_error(reply) or not reply.arguments():
                err_msg = reply.errorMessage() if reply.errorMessage() else "No arguments"

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
                    err_msg2 = reply2.errorMessage() if reply2.errorMessage() else "No arguments"
                    print(
                        f"[DEBUG Screen Capturer] KWin D-Bus error: {err_msg} | Fallback error: {err_msg2}"
                    )
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

        except Exception as ex:
            print(f"[DEBUG Screen Capturer] KWin ScreenShot2 grab exception: {ex}")
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
                    print(f"[DEBUG Screen Capturer] KDE Spectacle background capture active ({self.bin_path}).")
            except Exception as e:
                print(f"[DEBUG Screen Capturer] Spectacle probe notice: {e}")
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
        quality: int = 78,
        fps_limit: int = 60,
        use_444_chroma: bool = False,
        scale_ratio: float = 1.0,
    ):
        super().__init__()
        self.target_ip = target_ip
        self.pin = pin
        self.quality = quality
        self.fps_limit = fps_limit
        self.use_444_chroma = use_444_chroma
        self.scale_ratio = max(0.5, min(1.0, float(scale_ratio)))
        self.running = True
        self.paused = False
        self.send_cursorless_frame_once = False

        # Asynchronous frame queue and twin encoder workers
        self.frame_queue = queue.Queue(maxsize=2)
        self.pipeline_running = False

        # Telemetry locks
        self._stats_lock = threading.Lock()
        self.fps_frame_counter = 0
        self.total_capture_dur = 0.0
        self.total_encode_dur = 0.0
        self.total_send_dur = 0.0
        self.fps_last_report_time = time.perf_counter()

    def set_fps_limit(self, fps: int):
        self.fps_limit = max(1, fps)
        print(f"[DEBUG Sender Video] Dynamic framerate adjusted to: {self.fps_limit} FPS")

    def set_quality_params(self, quality: int, use_444: bool, scale_ratio: float = 1.0):
        self.quality = quality
        self.use_444_chroma = use_444
        self.scale_ratio = max(0.5, min(1.0, float(scale_ratio)))
        print(
            f"[DEBUG Sender Video] Quality: {self.quality}%, 4:4:4: {self.use_444_chroma}, Scale: {self.scale_ratio:.2f}x"
        )

    def trigger_cursorless_frame(self):
        self.send_cursorless_frame_once = True

    def _parallel_encoder_worker(self, sock: socket.socket, worker_id: int):
        """Concurrent worker that pulls raw buffers, compresses to JPEG, and sends to socket."""
        encode_params = [int(cv2.IMWRITE_JPEG_QUALITY), self.quality]
        if self.use_444_chroma:
            sampling_factor_id = getattr(cv2, "IMWRITE_JPEG_SAMPLING_FACTOR", 10)
            sampling_444_val = getattr(cv2, "IMWRITE_JPEG_SAMPLING_FACTOR_444", 0x00010001)
            encode_params.extend([int(sampling_factor_id), int(sampling_444_val)])

        while self.pipeline_running:
            try:
                item = self.frame_queue.get(timeout=0.03)
            except queue.Empty:
                continue

            frame_raw, t_cap_ms = item

            t_enc_start = time.perf_counter()

            # Fast downscale if requested
            if self.scale_ratio < 0.99:
                h, w = frame_raw.shape[:2]
                new_w = int(w * self.scale_ratio)
                new_h = int(h * self.scale_ratio)
                frame_to_encode = cv2.resize(frame_raw, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
            else:
                frame_to_encode = frame_raw

            data = None

            # Fast path 1: libjpeg-turbo SIMD directly on 4-channel BGRX
            if _TURBO_JPEG is not None:
                try:
                    if frame_to_encode.ndim == 3 and frame_to_encode.shape[2] == 4:
                        data = _TURBO_JPEG.encode(
                            frame_to_encode,
                            quality=self.quality,
                            pixel_format=TJPF_BGRX,
                            jpeg_subsample=TJSAMP_420,
                        )
                    else:
                        data = _TURBO_JPEG.encode(
                            frame_to_encode,
                            quality=self.quality,
                            pixel_format=TJPF_BGR,
                            jpeg_subsample=TJSAMP_420,
                        )
                except Exception:
                    data = None

            # Fast path 2: Standard OpenCV multi-channel encoding
            if data is None:
                if frame_to_encode.ndim == 3 and frame_to_encode.shape[2] == 4:
                    frame_bgr = cv2.cvtColor(frame_to_encode, cv2.COLOR_BGRA2BGR)
                else:
                    frame_bgr = frame_to_encode

                cur_params = [int(cv2.IMWRITE_JPEG_QUALITY), self.quality]
                if self.use_444_chroma:
                    sampling_factor_id = getattr(cv2, "IMWRITE_JPEG_SAMPLING_FACTOR", 10)
                    sampling_444_val = getattr(cv2, "IMWRITE_JPEG_SAMPLING_FACTOR_444", 0x00010001)
                    cur_params.extend([int(sampling_factor_id), int(sampling_444_val)])

                success, enc_img = cv2.imencode(".jpg", frame_bgr, cur_params)
                if success:
                    data = enc_img.tobytes()

            t_enc_end = time.perf_counter()

            # Concurrent network send
            t_send_start = time.perf_counter()
            if data and self.pipeline_running:
                try:
                    sock.sendall(struct.pack(">L", len(data)) + data)
                except Exception:
                    self.pipeline_running = False
                    break
            t_send_end = time.perf_counter()

            # Aggregate stats across both workers
            with self._stats_lock:
                self.fps_frame_counter += 1
                self.total_capture_dur += t_cap_ms
                self.total_encode_dur += (t_enc_end - t_enc_start) * 1000.0
                self.total_send_dur += (t_send_end - t_send_start) * 1000.0

                now = time.perf_counter()
                if now - self.fps_last_report_time >= 1.0:
                    elapsed_report = now - self.fps_last_report_time
                    measured_fps = self.fps_frame_counter / elapsed_report
                    avg_cap_ms = self.total_capture_dur / self.fps_frame_counter
                    avg_enc_ms = self.total_encode_dur / self.fps_frame_counter
                    avg_send_ms = self.total_send_dur / self.fps_frame_counter
                    avg_cycle_ms = 1000.0 / measured_fps if measured_fps > 0 else 0.0

                    print(
                        f"[DEBUG Sender Video] Live: {measured_fps:5.1f} FPS "
                        f"(Target: {self.fps_limit} FPS | Cycle: {avg_cycle_ms:4.1f}ms "
                        f"[Cap: {avg_cap_ms:4.1f}ms, Enc: {avg_enc_ms:4.1f}ms, Net: {avg_send_ms:4.1f}ms])"
                    )

                    self.fps_frame_counter = 0
                    self.total_capture_dur = 0.0
                    self.total_encode_dur = 0.0
                    self.total_send_dur = 0.0
                    self.fps_last_report_time = now

    def run(self):
        print(
            f"[DEBUG Sender Video] Connecting to {self.target_ip}:{VIDEO_PORT} "
            f"(PIN: '{self.pin}', Quality: {self.quality}, Scale: {self.scale_ratio:.2f}x)..."
        )
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, SOCKET_BUFFER_SIZE)
            except Exception as e:
                print(f"[DEBUG Sender Video] SO_SNDBUF notice: {e}")

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

        # Start twin parallel encoder workers for 120 FPS capable throughput
        self.pipeline_running = True
        w1 = threading.Thread(target=self._parallel_encoder_worker, args=(sock, 1), daemon=True)
        w2 = threading.Thread(target=self._parallel_encoder_worker, args=(sock, 2), daemon=True)
        w1.start()
        w2.start()

        screen = QGuiApplication.primaryScreen()
        screen_dpr = float(screen.devicePixelRatio()) if screen else 1.0

        with create_mss_instance() as sct:
            monitor = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
            mon_left = monitor.get("left", 0)
            mon_top = monitor.get("top", 0)

            while self.running and self.pipeline_running:
                if self.paused and not self.send_cursorless_frame_once:
                    time.sleep(0.03)
                    continue

                t_frame_start = time.perf_counter()

                try:
                    t_cap_start = time.perf_counter()

                    if use_kwin:
                        use_native_res = bool(self.quality >= 90)
                        # Offload cursor from KWin compositor for faster capture
                        frame_raw = kwin_grabber.grab(
                            include_cursor=False,
                            native_resolution=use_native_res,
                        )
                        if frame_raw is not None and not (self.paused or self.send_cursorless_frame_once):
                            render_cursor_on_frame(
                                frame_raw,
                                monitor_left=mon_left,
                                monitor_top=mon_top,
                                scale_factor=screen_dpr,
                            )
                    elif use_spectacle and spectacle_grabber:
                        frame_raw = spectacle_grabber.grab()
                        if frame_raw is not None and not (self.paused or self.send_cursorless_frame_once):
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
                        if not (self.paused or self.send_cursorless_frame_once):
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

                    if self.send_cursorless_frame_once:
                        self.send_cursorless_frame_once = False

                    if self.frame_queue.full():
                        try:
                            self.frame_queue.get_nowait()
                        except queue.Empty:
                            pass

                    self.frame_queue.put_nowait((frame_raw, cap_ms))

                except (socket.error, BrokenPipeError, ConnectionResetError) as e:
                    print(f"[DEBUG Sender Video] Network socket disconnected: {e}")
                    break
                except subprocess.TimeoutExpired:
                    time.sleep(0.002)
                    continue
                except Exception as e:
                    print(f"[DEBUG Sender Video] Frame capture notice: {e}")
                    time.sleep(0.002)
                    continue

                # Hybrid clock pacing (sleep + spin-wait for exact 16.6ms frame cadence)
                target_frame_time = 1.0 / max(1, self.fps_limit)
                frame_elapsed = time.perf_counter() - t_frame_start
                sleep_sec = target_frame_time - frame_elapsed
                if sleep_sec > 0.002:
                    time.sleep(sleep_sec - 0.001)
                while (time.perf_counter() - t_frame_start) < target_frame_time:
                    pass

        self.pipeline_running = False
        w1.join(timeout=0.3)
        w2.join(timeout=0.3)

        if kwin_grabber:
            kwin_grabber.cleanup()
        if spectacle_grabber:
            spectacle_grabber.cleanup()

        try:
            sock.close()
        except Exception:
            pass

        print("[DEBUG Sender Video] Video streaming thread stopped.")
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
                eff_vol = 0.0 if (self.muted or self.paused) else self.volume
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
                print(f"[DEBUG Sender Control] Reconnected control socket to {self.target_ip}:{CONTROL_PORT}")
                return True
            except Exception as e:
                print(f"[DEBUG Sender Control] Control socket connect failed: {e}")
                return False

    def send_command(self, cmd: dict):
        if not self._ensure_socket_connected():
            return
        with self._send_lock:
            if self.sock:
                try:
                    data = json.dumps(cmd).encode("utf-8")
                    self.sock.sendall(struct.pack(">L", len(data)) + data)
                except Exception as e:
                    print(f"[DEBUG Sender Control] Failed to transmit command: {e}")
                    try:
                        self.sock.close()
                    except Exception:
                        pass
                    self.sock = None

    def run(self):
        print(f"[DEBUG Sender Control] Connecting to {self.target_ip}:{CONTROL_PORT}...")
        self._ensure_socket_connected()

        with create_mss_instance() as sct:
            monitor = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
            scr_w, scr_h = monitor["width"], monitor["height"]
            mon_l, mon_t = monitor["left"], monitor["top"]

        injector = UniversalInputInjector(scr_w, scr_h, mon_left=mon_l, mon_top=mon_t)
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
                    print("[DEBUG Sender Control] Connection closed by receiver.")
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
            except Exception as ex:
                if self.running:
                    print(f"[DEBUG Sender Control] Socket read notice: {ex}")
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
                        injector.execute(event)
                except Exception as ex:
                    print(f"[DEBUG Sender Control] Event execution error: {ex}")

        injector.close()
        with self._send_lock:
            try:
                if self.sock:
                    self.sock.close()
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