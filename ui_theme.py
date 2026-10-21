# ui_theme.py

"""
UI Theming Data & Device Identifier Color Mapping.
"""

DEVICE_COLOR_PALETTES = [
    {"accent": "#00b4d8", "bg": "#122338", "border": "#1b4d75"},  # 0: Steam - Cyan
    {"accent": "#06d6a0", "bg": "#102c23", "border": "#195c47"},  # 1: CS - Mint
    {"accent": "#b388ff", "bg": "#2b1c3d", "border": "#5c3385"},  # 2: Violet
    {"accent": "#ffb703", "bg": "#382910", "border": "#7a5314"},  # 3: Amber
    {"accent": "#ff5c8a", "bg": "#381723", "border": "#7d2946"},  # 4: Coral
    {"accent": "#7986cb", "bg": "#1a1f3b", "border": "#39437d"},  # 5: Indigo
    {"accent": "#aeea00", "bg": "#25330e", "border": "#516e1a"},  # 6: Spring Lime
    {"accent": "#fb8500", "bg": "#3b210f", "border": "#7d3f15"},  # 7: Sunset Tangerine
    {"accent": "#2ec4b6", "bg": "#102e2c", "border": "#1b5e5a"},  # 8: Ocean Turquoise
    {"accent": "#f72585", "bg": "#361026", "border": "#7a1a52"},  # 9: Fuchsia Magenta
    {"accent": "#48cae4", "bg": "#112a36", "border": "#1e5b75"},  # 10: Bright Sky Blue
    {"accent": "#e63946", "bg": "#381419", "border": "#7d222b"},  # 11: Crimson Red
]

def get_device_theme(device_identifier: str) -> dict:
    """Returns a deterministic, unique color shade theme based on the computer name or IP."""
    raw = str(device_identifier).strip().lower()
    if not raw or "steam" in raw:
        return DEVICE_COLOR_PALETTES[0]
    if "cs" in raw:
        return DEVICE_COLOR_PALETTES[1]

    h = 0
    for ch in raw:
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    return DEVICE_COLOR_PALETTES[h % len(DEVICE_COLOR_PALETTES)]