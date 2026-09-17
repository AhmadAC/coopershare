#################### START OF FILE: config.py ####################

# config.py

"""
Global configuration constants, socket buffer tuning, and environment flags.
Tuned for high-framerate 60 FPS streaming across 1Gbps and Wi-Fi 6 LAN networks.
"""

import sys

VIDEO_PORT = 9988
CONTROL_PORT = 9989
AUDIO_PORT = 9990
DISCOVERY_PORT = 9991
REVERSE_VIDEO_PORT = 9992

DEFAULT_SAMPLE_RATE = 48000
CHANNELS = 2

# Enlarged TCP window buffers to prevent packet queueing stalls at 60 FPS
SOCKET_BUFFER_SIZE = 8 * 1024 * 1024
SOCKET_CHUNK_SIZE = 128 * 1024

try:
    import sounddevice as sd

    AUDIO_AVAILABLE = True
except Exception:
    AUDIO_AVAILABLE = False

IS_WIN = sys.platform == "win32"
IS_LINUX = sys.platform.startswith("linux")