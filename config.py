
"""
Global configuration constants and environment flags.
"""

import sys

VIDEO_PORT = 9988
CONTROL_PORT = 9989
AUDIO_PORT = 9990
DISCOVERY_PORT = 9991
REVERSE_VIDEO_PORT = 9992

DEFAULT_SAMPLE_RATE = 48000
CHANNELS = 2
SOCKET_BUFFER_SIZE = 4 * 1024 * 1024

try:
    import sounddevice as sd

    AUDIO_AVAILABLE = True
except Exception:
    AUDIO_AVAILABLE = False

IS_WIN = sys.platform == "win32"
IS_LINUX = sys.platform.startswith("linux")
