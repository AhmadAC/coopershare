"""
Background network worker threads for video, audio, input, reverse video, and beacon discovery.
"""

import json
import socket
import struct
import threading
import time
from typing import Optional

import cv2
import numpy as np
from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage

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
from utils import create_mss_instance, recv_exact
from video_backend import render_cursor_on_frame

if AUDIO_AVAILABLE:
    import sounddevice as sd


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
        print(
            f"[DEBUG Sender Video] Dynamic quality adjusted to: {self.quality}% (4:4:4={self.use_444_chroma})"
        )

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
                        sampling_444_val = getattr(
                            cv2, "IMWRITE_JPEG_SAMPLING_FACTOR_444", 0x00010001
                        )
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
        """Transmits remote input / control packets to the receiver display with auto-reconnection."""
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
            mon = sct.monitors[1]
            scr_w, scr_h = mon["width"], mon["height"]
            mon_l, mon_t = mon["left"], mon["top"]

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