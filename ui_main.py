#################### START OF FILE: ui_main.py ####################

# ui_main.py

"""
Main Floating Frameless Controller UI, Collapsed Mini Pill, Context Menu & Remote Timer Dialog.
Features persistent state loading and debounced saving to history.json (Quality preset, FPS, Volume, Opacity, PIN, etc.),
with computer-specific dynamic color shades applied exclusively to the target computer dropdown box
(Steam, CS, and distinct hashed shades for any other device/IP),
vector SVG icons, dynamic audio-pause toggle feedback, full Linux Wayland/X11 move & opacity support,
cross-platform physical host mute control (Windows WASAPI & Linux PipeWire/WirePlumber),
toggleable Remote TV Viewer session controller, live 1-second GUI FPS counter, Windows DWM capture exclusion,
multi-IP friendly name manager for TVs, keyboard arrow navigation for device dropdown,
and z-order guarded topmost dropdown popups that always render in front of the GUI on Windows 11.
Includes full verbose terminal diagnostics.
"""

import ctypes
import math
import re
import sys
import time
from typing import Optional

from PySide6.QtCore import QByteArray, QEvent, QPoint, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QGuiApplication, QKeyEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from audio_backend import HostAudioController
from threads import (
    AudioSenderThread,
    DiscoveryListenerThread,
    InputReceiverThread,
    ScreenSenderThread,
)
from ui_viewer import RemoteReceiverViewerWindow
from utils import (
    SVG_CHEVRON_DOWN,
    SVG_CLOSE,
    SVG_DISPLAY,
    SVG_LOGOUT,
    SVG_MAXIMIZE,
    SVG_MINIMIZE,
    SVG_PAUSE,
    SVG_PLAY,
    SVG_RESTORE,
    SVG_SCREEN,
    SVG_STATUS_DOT,
    SVG_TAG,
    SVG_TIMER,
    SVG_TRASH,
    SVG_VOLUME_MUTE,
    SVG_VOLUME_ON,
    create_application_icon,
    exclude_from_capture,
    load_history,
    save_history,
    svg_to_icon,
    svg_to_pixmap,
)

# ---------------------------------------------------------------------------
# Distinct Computer Color Shade Palettes (Exclusively for Target Dropdown)
# ---------------------------------------------------------------------------
DEVICE_COLOR_PALETTES = [
    {  # 0: Steam - Cyan / Deep Oceanic Slate
        "accent": "#00b4d8",
        "bg": "#122338",
        "border": "#1b4d75",
    },
    {  # 1: CS - Mint / Emerald Green
        "accent": "#06d6a0",
        "bg": "#102c23",
        "border": "#195c47",
    },
    {  # 2: Violet / Electric Purple
        "accent": "#b388ff",
        "bg": "#2b1c3d",
        "border": "#5c3385",
    },
    {  # 3: Amber / Golden Orange
        "accent": "#ffb703",
        "bg": "#382910",
        "border": "#7a5314",
    },
    {  # 4: Coral / Rose Pink
        "accent": "#ff5c8a",
        "bg": "#381723",
        "border": "#7d2946",
    },
    {  # 5: Indigo / Sapphire
        "accent": "#7986cb",
        "bg": "#1a1f3b",
        "border": "#39437d",
    },
    {  # 6: Spring Lime Green
        "accent": "#aeea00",
        "bg": "#25330e",
        "border": "#516e1a",
    },
    {  # 7: Sunset Tangerine
        "accent": "#fb8500",
        "bg": "#3b210f",
        "border": "#7d3f15",
    },
    {  # 8: Ocean Turquoise
        "accent": "#2ec4b6",
        "bg": "#102e2c",
        "border": "#1b5e5a",
    },
    {  # 9: Fuchsia Magenta
        "accent": "#f72585",
        "bg": "#361026",
        "border": "#7a1a52",
    },
    {  # 10: Bright Sky Blue
        "accent": "#48cae4",
        "bg": "#112a36",
        "border": "#1e5b75",
    },
    {  # 11: Crimson Red
        "accent": "#e63946",
        "bg": "#381419",
        "border": "#7d222b",
    },
]


def get_device_theme(device_identifier: str) -> dict:
    """Returns a deterministic, unique color shade theme based on the computer name or IP."""
    raw = str(device_identifier).strip().lower()
    if not raw:
        return DEVICE_COLOR_PALETTES[0]

    # Explicit computer name keywords
    if "steam" in raw:
        return DEVICE_COLOR_PALETTES[0]
    if "cs" in raw:
        return DEVICE_COLOR_PALETTES[1]

    # Deterministic hash to distribute any other computer name or IP across distinct shades
    h = 0
    for ch in raw:
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    return DEVICE_COLOR_PALETTES[h % len(DEVICE_COLOR_PALETTES)]


class TimerDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Receiver Timer")
        self.setFixedWidth(360)
        self.setStyleSheet(
            """
            QDialog {
                background-color: #1a1e29;
                border: 1px solid #3d475f;
                border-radius: 10px;
            }
            QLabel {
                color: #ffffff;
                font-family: 'Segoe UI', sans-serif;
                font-size: 12px;
            }
            QLineEdit {
                background: #262c3b;
                border: 1px solid #3d475f;
                color: #ffffff;
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 13px;
            }
            QPushButton {
                background-color: #0078d4;
                color: white;
                border: none;
                border-radius: 6px;
                font-weight: bold;
                font-size: 12px;
                padding: 7px 18px;
            }
            QPushButton:hover {
                background-color: #106ebe;
            }
            """
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        header_layout = QHBoxLayout()
        header_layout.setSpacing(8)
        timer_icon_lbl = QLabel()
        timer_icon_lbl.setPixmap(svg_to_pixmap(SVG_TIMER, 18, 18, "#00d084"))
        title_lbl = QLabel("Set Receiver Timer")
        title_lbl.setStyleSheet("font-weight: bold; font-size: 13px; color: #00d084;")
        header_layout.addWidget(timer_icon_lbl)
        header_layout.addWidget(title_lbl)
        header_layout.addStretch()
        layout.addLayout(header_layout)

        desc_lbl = QLabel("Enter duration (e.g. 30, 5m, 10:00, or raw number):")
        layout.addWidget(desc_lbl)

        input_layout = QHBoxLayout()
        self.timer_edit = QLineEdit()
        self.timer_edit.setPlaceholderText("30, 5m...")
        self.timer_edit.returnPressed.connect(self.accept)

        self.timer_btn = QPushButton("Start Timer")
        self.timer_btn.setIcon(svg_to_icon(SVG_TIMER, 14, "#ffffff"))
        self.timer_btn.clicked.connect(self.accept)

        input_layout.addWidget(self.timer_edit)
        input_layout.addWidget(self.timer_btn)
        layout.addLayout(input_layout)

        if sys.platform == "win32":
            exclude_from_capture(self)

    def showEvent(self, event):
        if sys.platform == "win32":
            exclude_from_capture(self)
        super().showEvent(event)

    def get_args(self) -> str:
        return self.timer_edit.text().strip().strip('"').strip("'")


class EditTvDialog(QDialog):
    """Dialog allowing users to set a friendly TV name and assign one or more IP addresses."""

    def __init__(self, tv_name: str, ips: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Configure TV & Friendly Name")
        self.setFixedWidth(440)
        self.setStyleSheet(
            """
            QDialog {
                background-color: #1a1e29;
                border: 1px solid #3d475f;
                border-radius: 10px;
            }
            QLabel {
                color: #ffffff;
                font-family: 'Segoe UI', sans-serif;
                font-size: 12px;
            }
            QLineEdit {
                background: #262c3b;
                border: 1px solid #3d475f;
                color: #ffffff;
                border-radius: 6px;
                padding: 7px 10px;
                font-size: 13px;
            }
            QPushButton {
                background-color: #0078d4;
                color: white;
                border: none;
                border-radius: 6px;
                font-weight: bold;
                font-size: 12px;
                padding: 7px 18px;
            }
            QPushButton:hover {
                background-color: #106ebe;
            }
            QPushButton#cancel_btn {
                background-color: #262c3b;
                border: 1px solid #3d475f;
            }
            QPushButton#cancel_btn:hover {
                background-color: #333c4d;
            }
            """
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        header_layout = QHBoxLayout()
        header_layout.setSpacing(8)
        tag_icon = QLabel()
        tag_icon.setPixmap(svg_to_pixmap(SVG_TAG, 18, 18, "#00a2ed"))
        title_lbl = QLabel("Manage TV Friendly Name & IPs")
        title_lbl.setStyleSheet("font-weight: bold; font-size: 14px; color: #00a2ed;")
        header_layout.addWidget(tag_icon)
        header_layout.addWidget(title_lbl)
        header_layout.addStretch()
        layout.addLayout(header_layout)

        layout.addWidget(QLabel("TV Friendly Name:"))
        self.name_edit = QLineEdit(tv_name)
        self.name_edit.setPlaceholderText("e.g. Living Room TV, STEAM 408")
        self.name_edit.returnPressed.connect(self.accept)
        layout.addWidget(self.name_edit)

        layout.addWidget(QLabel("IP Address(es) (separate multiple with commas or spaces):"))
        self.ips_edit = QLineEdit(", ".join(ips))
        self.ips_edit.setPlaceholderText("e.g. 172.31.60.175, 192.168.90.221")
        self.ips_edit.returnPressed.connect(self.accept)
        layout.addWidget(self.ips_edit)

        hint_lbl = QLabel("Tip: You can save multiple IPs (e.g. Wi-Fi & LAN) for the same TV.")
        hint_lbl.setStyleSheet("color: #8f9bb3; font-size: 11px;")
        layout.addWidget(hint_lbl)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setObjectName("cancel_btn")
        self.cancel_btn.clicked.connect(self.reject)

        self.save_btn = QPushButton("Save")
        self.save_btn.clicked.connect(self.accept)

        btn_layout.addWidget(self.cancel_btn)
        btn_layout.addWidget(self.save_btn)
        layout.addLayout(btn_layout)

        if sys.platform == "win32":
            exclude_from_capture(self)

    def showEvent(self, event):
        if sys.platform == "win32":
            exclude_from_capture(self)
        super().showEvent(event)

    def get_data(self) -> tuple[str, list[str]]:
        name = self.name_edit.text().strip()
        raw_ips = self.ips_edit.text().strip()
        tokens = re.split(r"[,;\s]+", raw_ips)
        cleaned_ips = []
        for t in tokens:
            t_clean = t.strip()
            if t_clean and t_clean not in cleaned_ips:
                cleaned_ips.append(t_clean)
        return name, cleaned_ips


class TopmostComboBox(QComboBox):
    """QComboBox that guarantees its popup container stays in front of topmost parent windows."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._is_popup_open = False

        container = self.view().window()
        if container:
            container.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)

        view = self.view()
        view.setStyleSheet(
            """
            QListView {
                background-color: #1a1e29;
                color: #ffffff;
                border: 1px solid #3d475f;
                border-radius: 6px;
                outline: none;
                padding: 4px;
                font-family: 'Segoe UI', sans-serif;
                font-size: 12px;
            }
            QListView::item {
                min-height: 24px;
                padding: 4px 8px;
                border-radius: 4px;
                color: #ffffff;
            }
            QListView::item:selected, QListView::item:hover {
                background-color: #0078d4;
                color: #ffffff;
            }
            """
        )

    def showPopup(self):
        self._is_popup_open = True
        super().showPopup()

        popup = self.view().window()
        if popup:
            popup.raise_()
            if sys.platform == "win32":
                try:
                    hwnd = int(popup.winId())
                    GWL_EXSTYLE = -20
                    WS_EX_TOPMOST = 0x00000008
                    ex_style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
                    ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex_style | WS_EX_TOPMOST)
                    HWND_TOPMOST = -1
                    ctypes.windll.user32.SetWindowPos(
                        hwnd,
                        HWND_TOPMOST,
                        0,
                        0,
                        0,
                        0,
                        0x0001 | 0x0002 | 0x0040,
                    )
                    ctypes.windll.user32.BringWindowToTop(hwnd)
                    exclude_from_capture(hwnd)
                except Exception:
                    pass
            popup.raise_()

    def hidePopup(self):
        self._is_popup_open = False
        super().hidePopup()
        parent_win = self.window()
        if parent_win and hasattr(parent_win, "enforce_always_on_top"):
            QTimer.singleShot(100, parent_win.enforce_always_on_top)


class FloatingSenderWindow(QWidget):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("MrCoopersScreenShare - Sender")
        self.setWindowIcon(create_application_icon())

        self.history_data = load_history()

        self.stream_thread: Optional[ScreenSenderThread] = None
        self.audio_thread: Optional[AudioSenderThread] = None
        self.control_thread: Optional[InputReceiverThread] = None
        self.viewer_window: Optional[RemoteReceiverViewerWindow] = None

        self._drag_start_pos = QPoint()
        self._window_start_pos = QPoint()
        self._is_dragging = False

        self.is_mini_mode = False
        self.is_paused = False
        self.is_stream_muted = False
        self.is_host_muted = False
        self.input_enabled = bool(self.history_data.get("touch_input", True))
        self.allow_audio_when_paused = bool(self.history_data.get("allow_audio_when_paused", False))

        saved_vol = self.history_data.get("tv_volume", 100)
        self.tv_volume = (max(0, min(150, int(saved_vol))) if isinstance(saved_vol, (int, float)) else 100) / 100.0

        self.discovered_ip = ""
        self.pin_required = False
        self.status_color = "#8f9bb3"
        self.current_opacity = 0.94
        self.was_streaming_before_viewing = False

        self.is_pulsing = False
        self.pulse_start_time = 0.0

        self._save_debounce_timer = QTimer(self)
        self._save_debounce_timer.setSingleShot(True)
        self._save_debounce_timer.setInterval(400)
        self._save_debounce_timer.timeout.connect(self._flush_history_save)

        self.topmost_timer = QTimer(self)
        self.topmost_timer.setInterval(500)
        self.topmost_timer.timeout.connect(self._on_topmost_timer)
        self.topmost_timer.start()

        self.pulse_timer = QTimer(self)
        self.pulse_timer.setInterval(40)
        self.pulse_timer.timeout.connect(self._on_pulse_step)

        self._init_window()
        self._setup_ui()
        self._populate_device_list()

        initial_target = self.device_combo.currentText().strip() or self.get_selected_target_ip()
        self._update_device_combo_style(initial_target)

        self.discovery_thread = DiscoveryListenerThread()
        self.discovery_thread.device_found.connect(self.on_device_discovered)
        self.discovery_thread.start()
        print("[Sender-Main] UDP discovery listener active on port 9991.", flush=True)

    def _schedule_history_save(self):
        self._save_debounce_timer.start()

    def _flush_history_save(self):
        save_history(self.history_data)

    def _init_window(self):
        self.setWindowFlags(
            Qt.Window
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.WindowMinimizeButtonHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        saved_op = self.history_data.get("opacity", 94)
        op_val = max(20, min(100, int(saved_op))) if isinstance(saved_op, (int, float)) else 94
        self.current_opacity = op_val / 100.0
        self.apply_opacity(self.current_opacity)

        if sys.platform == "win32":
            try:
                hwnd = int(self.winId())
                GWL_STYLE = -16
                WS_MINIMIZEBOX = 0x00020000
                WS_SYSMENU = 0x00080000
                style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_STYLE)
                ctypes.windll.user32.SetWindowLongW(
                    hwnd, GWL_STYLE, style | WS_MINIMIZEBOX | WS_SYSMENU
                )
            except Exception:
                pass
            exclude_from_capture(self)

        self.enforce_always_on_top()

    def is_any_popup_open(self) -> bool:
        for combo in (
            getattr(self, "device_combo", None),
            getattr(self, "fps_combo", None),
            getattr(self, "quality_combo", None),
        ):
            if combo is not None:
                if getattr(combo, "_is_popup_open", False):
                    return True
                try:
                    view = combo.view()
                    if view and view.isVisible():
                        return True
                except Exception:
                    pass
        return False

    def apply_opacity(self, opacity: float):
        self.current_opacity = max(0.1, min(1.0, float(opacity)))

        if QApplication.platformName() != "wayland":
            try:
                super().setWindowOpacity(self.current_opacity)
            except Exception:
                pass

        self._update_card_style()
        if hasattr(self, "mini_bar"):
            self._update_mini_bar_style()

    def enforce_always_on_top(self):
        if self.is_any_popup_open():
            return

        if sys.platform == "win32":
            try:
                hwnd = int(self.winId())
                GWL_EXSTYLE = -20
                WS_EX_TOPMOST = 0x00000008
                ex_style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
                if not (ex_style & WS_EX_TOPMOST):
                    ctypes.windll.user32.SetWindowLongW(
                        hwnd, GWL_EXSTYLE, ex_style | WS_EX_TOPMOST
                    )

                HWND_TOPMOST = -1
                ctypes.windll.user32.SetWindowPos(
                    hwnd,
                    HWND_TOPMOST,
                    0,
                    0,
                    0,
                    0,
                    0x0002 | 0x0001 | 0x0010 | 0x0040,
                )
            except Exception:
                pass
        self.raise_()

    def _on_topmost_timer(self):
        if self.is_any_popup_open():
            return
        if self.is_mini_mode and not self.isMinimized():
            self.enforce_always_on_top()

    def trigger_mini_pulse(self):
        self.is_pulsing = True
        self.pulse_start_time = time.time()
        self._update_mini_bar_style(override_color="#ff3333")
        self.pulse_timer.start()

    def _on_pulse_step(self):
        elapsed = time.time() - self.pulse_start_time
        if elapsed >= 3.0 or not self.is_mini_mode:
            self.pulse_timer.stop()
            self.is_pulsing = False
            self.apply_opacity(0.35)
            self._update_mini_bar_style()
            return

        osc = (math.sin(elapsed * math.pi * 3.5) + 1.0) / 2.0
        current_op = 0.30 + (0.60 * osc)
        self.apply_opacity(current_op)
        self.enforce_always_on_top()

    def changeEvent(self, event: QEvent):
        if event.type() in (QEvent.WindowStateChange, QEvent.ActivationChange):
            if self.is_any_popup_open():
                super().changeEvent(event)
                return
            if self.is_mini_mode:
                if self.isMinimized():
                    self.showNormal()
                self.enforce_always_on_top()
                self.trigger_mini_pulse()
            else:
                if not self.isMinimized():
                    self.enforce_always_on_top()
                    if hasattr(self, "opacity_slider"):
                        self.apply_opacity(self.opacity_slider.value() / 100.0)
        super().changeEvent(event)

    def _update_device_combo_style(self, device_text: Optional[str] = None):
        """Applies distinct computer color shades strictly to the target dropdown box."""
        if not hasattr(self, "device_combo"):
            return
        if device_text is None:
            device_text = self.device_combo.currentText().strip() or self.get_selected_target_ip()

        theme = get_device_theme(device_text)
        accent = theme["accent"]
        bg = theme["bg"]
        border = theme["border"]

        self.device_combo.setStyleSheet(
            f"""
            QComboBox {{
                background-color: {bg};
                border: 1.5px solid {border};
                color: #ffffff;
                border-radius: 6px;
                padding: 5px 8px;
                font-size: 12px;
                font-weight: bold;
            }}
            QComboBox:hover, QComboBox:focus {{
                border: 1.5px solid {accent};
            }}
            QComboBox QLineEdit {{
                background: transparent;
                border: none;
                color: #ffffff;
                font-size: 12px;
                font-weight: bold;
                padding: 0px;
            }}
            QComboBox::drop-down {{
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 22px;
                border-left-width: 0px;
                border-top-right-radius: 6px;
                border-bottom-right-radius: 6px;
            }}
            QComboBox::down-arrow {{
                width: 0px;
                height: 0px;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 5px solid {accent};
                margin-right: 6px;
            }}
            QComboBox::down-arrow:hover {{
                border-top: 5px solid #ffffff;
            }}
            QComboBox QAbstractItemView {{
                background-color: #1a1e29;
                color: #ffffff;
                border: 1px solid #3d475f;
                border-radius: 6px;
                selection-background-color: #0078d4;
                selection-color: #ffffff;
                outline: none;
                padding: 4px;
            }}
            QComboBox QAbstractItemView::item {{
                min-height: 24px;
                padding: 4px 8px;
                border-radius: 4px;
                color: #ffffff;
            }}
            QComboBox QAbstractItemView::item:selected,
            QComboBox QAbstractItemView::item:hover {{
                background-color: #0078d4;
                color: #ffffff;
            }}
            """
        )

    def _update_card_style(self):
        """Preserves the clean default application theme across all standard windows & widgets."""
        if not hasattr(self, "card"):
            return
        alpha = self.current_opacity if not self.is_mini_mode else 1.0
        self.card.setStyleSheet(
            f"""
            QFrame#card {{
                background-color: rgba(26, 30, 41, {alpha:.3f});
                border: 1px solid rgba(51, 60, 77, {min(1.0, alpha + 0.1):.3f});
                border-radius: 14px;
            }}
            QLabel {{ color: #ffffff; font-family: 'Segoe UI', sans-serif; font-size: 12px; }}
            QLineEdit, QComboBox {{
                background: #262c3b; border: 1px solid #3d475f;
                color: #ffffff; border-radius: 6px; padding: 5px 8px; font-size: 12px;
            }}
            QComboBox::drop-down {{
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 22px;
                border-left-width: 0px;
                border-top-right-radius: 6px;
                border-bottom-right-radius: 6px;
            }}
            QComboBox::down-arrow {{
                width: 0px;
                height: 0px;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 5px solid #8f9bb3;
                margin-right: 6px;
            }}
            QComboBox::down-arrow:hover {{
                border-top: 5px solid #ffffff;
            }}
            QComboBox QAbstractItemView {{
                background-color: #1a1e29;
                color: #ffffff;
                border: 1px solid #3d475f;
                border-radius: 6px;
                selection-background-color: #0078d4;
                selection-color: #ffffff;
                outline: none;
                padding: 4px;
            }}
            QComboBox QAbstractItemView::item {{
                min-height: 24px;
                padding: 4px 8px;
                border-radius: 4px;
                color: #ffffff;
            }}
            QComboBox QAbstractItemView::item:selected,
            QComboBox QAbstractItemView::item:hover {{
                background-color: #0078d4;
                color: #ffffff;
            }}
            QPushButton {{
                background-color: #0078d4; color: white; border: none;
                border-radius: 6px; font-weight: bold; font-size: 11px; padding: 6px 10px;
            }}
            QPushButton:hover {{ background-color: #106ebe; }}
            QCheckBox {{ color: #8f9bb3; font-size: 11px; }}
            QSlider::groove:horizontal {{ height: 4px; background: #333c4e; border-radius: 2px; }}
            QSlider::handle:horizontal {{ background: #00a2ed; width: 12px; margin: -4px 0; border-radius: 6px; }}
            """
        )
        self._update_device_combo_style()

    def _setup_ui(self):
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        self.stack = QStackedWidget(self)

        self.card = QFrame()
        self.card.setObjectName("card")
        self._update_card_style()

        self.card_layout = QVBoxLayout(self.card)
        self.card_layout.setContentsMargins(12, 10, 12, 10)
        self.card_layout.setSpacing(8)

        # Header Bar
        self.header_bar = QWidget()
        h_layout = QHBoxLayout(self.header_bar)
        h_layout.setContentsMargins(0, 0, 0, 0)
        h_layout.setSpacing(6)

        self.status_dot = QLabel()
        self.status_dot.setFixedSize(14, 14)
        self._update_status_dot(self.status_color)

        self.title_lbl = QLabel("MrCoopersScreenShare")
        self.title_lbl.setStyleSheet("font-weight: bold; color: #00a2ed;")

        self.fps_badge = QLabel("")
        self.fps_badge.setVisible(False)
        self.fps_badge.setStyleSheet(
            "color: #00d084; font-weight: bold; font-size: 11px; padding: 1px 5px; "
            "background: rgba(0, 208, 132, 0.15); border: 1px solid rgba(0, 208, 132, 0.35); border-radius: 4px;"
        )

        self.collapse_btn = QPushButton()
        self.collapse_btn.setIcon(svg_to_icon(SVG_CHEVRON_DOWN, 12, "#8f9bb3"))
        self.collapse_btn.setIconSize(QSize(12, 12))
        self.collapse_btn.setToolTip("Collapse to mini floating indicator")
        self.collapse_btn.setFixedSize(24, 24)
        self.collapse_btn.setStyleSheet(
            "background: #262c3b; border-radius: 12px; padding: 0px;"
        )
        self.collapse_btn.clicked.connect(self.collapse_to_mini)

        self.close_btn = QPushButton()
        self.close_btn.setIcon(svg_to_icon(SVG_CLOSE, 12, "#ff6b6b"))
        self.close_btn.setIconSize(QSize(12, 12))
        self.close_btn.setFixedSize(24, 24)
        self.close_btn.setStyleSheet(
            "background: #332228; border-radius: 12px; padding: 0px;"
        )
        self.close_btn.clicked.connect(self.close)

        h_layout.addWidget(self.status_dot)
        h_layout.addWidget(self.title_lbl)
        h_layout.addWidget(self.fps_badge)
        h_layout.addStretch()
        h_layout.addWidget(self.collapse_btn)
        h_layout.addWidget(self.close_btn)

        self.card_layout.addWidget(self.header_bar)

        # Row 1: Target Device Dropdown / IP Selector + Share Button
        ip_row = QHBoxLayout()
        self.device_combo = TopmostComboBox()
        self.device_combo.setEditable(True)
        self.device_combo.setFocusPolicy(Qt.StrongFocus)
        self.device_combo.setPlaceholderText("Select TV or Enter IP...")
        self.device_combo.lineEdit().setFocusPolicy(Qt.StrongFocus)
        self.device_combo.lineEdit().returnPressed.connect(self.toggle_connect)
        self.device_combo.currentIndexChanged.connect(self._on_device_index_changed)
        self.device_combo.editTextChanged.connect(self._on_device_text_changed)

        self.device_combo.installEventFilter(self)
        self.device_combo.lineEdit().installEventFilter(self)

        self.connect_btn = QPushButton("Share")
        self.connect_btn.clicked.connect(self.toggle_connect)

        ip_row.addWidget(self.device_combo, 1)
        ip_row.addWidget(self.connect_btn)
        self.card_layout.addLayout(ip_row)

        # Row 2: Live-Adjustable FPS & Ultra-Quality Preset Selector
        qual_row = QHBoxLayout()
        self.fps_combo = TopmostComboBox()
        self.fps_combo.addItems(["60 FPS", "30 FPS", "120 FPS", "15 FPS"])
        saved_fps = self.history_data.get("fps_preset", "")
        if saved_fps:
            matched_f = -1
            for i in range(self.fps_combo.count()):
                txt = self.fps_combo.itemText(i)
                if str(saved_fps).lower() in txt.lower() or txt.lower() in str(saved_fps).lower():
                    matched_f = i
                    break
            self.fps_combo.setCurrentIndex(matched_f if matched_f != -1 else 0)
        else:
            self.fps_combo.setCurrentIndex(0)
        self.fps_combo.currentIndexChanged.connect(self.on_fps_changed)

        self.quality_combo = TopmostComboBox()
        self.quality_combo.addItems(
            [
                "Ultra Crisp (60 FPS, 89%)",
                "Pixel-Perfect 1:1 (95% 4:4:4)",
                "Maximum Detail (98% 4:4:4)",
                "High Quality (82%)",
                "Balanced (72%)",
                "Studio 4:4:4 (88%)",
            ]
        )
        saved_quality = self.history_data.get("quality_preset", "")
        if saved_quality:
            matched_q = -1
            for i in range(self.quality_combo.count()):
                txt = self.quality_combo.itemText(i)
                if "98%" in str(saved_quality) and "98%" in txt:
                    matched_q = i
                    break
                elif "pixel-perfect" in str(saved_quality).lower() and "pixel-perfect" in txt.lower():
                    matched_q = i
                    break
                elif "ultra" in str(saved_quality).lower() and "ultra" in txt.lower():
                    matched_q = i
                    break
                elif "studio" in str(saved_quality).lower() and "studio" in txt.lower():
                    matched_q = i
                    break
                elif "high" in str(saved_quality).lower() and "high" in txt.lower():
                    matched_q = i
                    break
                elif "balanced" in str(saved_quality).lower() and "balanced" in txt.lower():
                    matched_q = i
                    break
            self.quality_combo.setCurrentIndex(matched_q if matched_q != -1 else 0)
        else:
            self.quality_combo.setCurrentIndex(0)
        self.quality_combo.currentIndexChanged.connect(self.on_quality_changed)

        qual_row.addWidget(self.fps_combo)
        qual_row.addWidget(self.quality_combo)
        self.card_layout.addLayout(qual_row)

        # Row 3: Auto-connect & Touch Input toggles + PIN input
        auto_row = QHBoxLayout()
        self.auto_connect_cb = QCheckBox("Auto-Connect")
        saved_auto = self.history_data.get("auto_connect", True)
        self.auto_connect_cb.setChecked(bool(saved_auto))
        self.auto_connect_cb.toggled.connect(self.on_auto_connect_toggled)

        self.touch_input_cb = QCheckBox("TV Touch Control")
        saved_touch = self.history_data.get("touch_input", True)
        self.touch_input_cb.setChecked(bool(saved_touch))
        self.input_enabled = bool(saved_touch)
        self.touch_input_cb.toggled.connect(self.on_touch_input_toggled)

        self.pin_input = QLineEdit()
        self.pin_input.setPlaceholderText("PIN (if req.)")
        self.pin_input.setMaxLength(4)
        self.pin_input.setFixedWidth(90)
        saved_pin = self.history_data.get("pin", "")
        if saved_pin:
            self.pin_input.setText(str(saved_pin))
        self.pin_input.returnPressed.connect(self.toggle_connect)
        self.pin_input.textChanged.connect(self.on_pin_text_changed)

        auto_row.addWidget(self.auto_connect_cb)
        auto_row.addWidget(self.touch_input_cb)
        auto_row.addStretch()
        auto_row.addWidget(self.pin_input)
        self.card_layout.addLayout(auto_row)

        # Row 4: Action Buttons (Pause/Resume, TV Audio Mute, Host Speaker Mute)
        btn_row = QHBoxLayout()
        self.pause_btn = QPushButton("Pause")
        self.pause_btn.setIcon(svg_to_icon(SVG_PAUSE, 14, "#ffffff"))
        self.pause_btn.clicked.connect(self.toggle_pause)
        self.pause_btn.setEnabled(False)

        self.stream_mute_btn = QPushButton("TV Audio")
        self.stream_mute_btn.setIcon(svg_to_icon(SVG_VOLUME_ON, 14, "#ffffff"))
        self.stream_mute_btn.clicked.connect(self.toggle_stream_mute)
        self.stream_mute_btn.setEnabled(False)

        self.host_mute_btn = QPushButton("Mute Host")
        self.host_mute_btn.setIcon(svg_to_icon(SVG_VOLUME_MUTE, 14, "#ffffff"))
        self.host_mute_btn.clicked.connect(self.toggle_host_mute)

        btn_row.addWidget(self.pause_btn)
        btn_row.addWidget(self.stream_mute_btn)
        btn_row.addWidget(self.host_mute_btn)
        self.card_layout.addLayout(btn_row)

        # Row 5: TV Volume Slider
        tv_vol_row = QHBoxLayout()
        tv_vol_lbl_title = QLabel("TV Vol:")
        tv_vol_lbl_title.setFixedWidth(46)
        self.tv_vol_slider = QSlider(Qt.Horizontal)
        self.tv_vol_slider.setRange(0, 150)
        saved_vol = self.history_data.get("tv_volume", 100)
        vol_val = max(0, min(150, int(saved_vol))) if isinstance(saved_vol, (int, float)) else 100
        self.tv_volume = vol_val / 100.0
        self.tv_vol_slider.setValue(vol_val)
        self.tv_vol_slider.valueChanged.connect(self.on_tv_volume_changed)

        self.tv_vol_val_lbl = QLabel(f"{vol_val}%")
        self.tv_vol_val_lbl.setFixedWidth(34)
        self.tv_vol_val_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        tv_vol_row.addWidget(tv_vol_lbl_title)
        tv_vol_row.addWidget(self.tv_vol_slider)
        tv_vol_row.addWidget(self.tv_vol_val_lbl)
        self.card_layout.addLayout(tv_vol_row)

        # Row 6: Opacity Slider
        trans_row = QHBoxLayout()
        op_lbl_title = QLabel("Opacity:")
        op_lbl_title.setFixedWidth(46)
        self.opacity_slider = QSlider(Qt.Horizontal)
        self.opacity_slider.setRange(20, 100)
        saved_op = self.history_data.get("opacity", 94)
        op_val = max(20, min(100, int(saved_op))) if isinstance(saved_op, (int, float)) else 94
        self.opacity_slider.setValue(op_val)
        self.opacity_slider.valueChanged.connect(self.on_opacity_changed)

        self.op_val_lbl = QLabel(f"{op_val}%")
        self.op_val_lbl.setFixedWidth(34)
        self.op_val_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        trans_row.addWidget(op_lbl_title)
        trans_row.addWidget(self.opacity_slider)
        trans_row.addWidget(self.op_val_lbl)
        self.card_layout.addLayout(trans_row)

        # View 2: Collapsed Mini Pill Indicator
        self.mini_container = QFrame()
        self.mini_container.setObjectName("mini_container")
        self.mini_container.setFixedSize(48, 16)
        self.mini_container.setCursor(Qt.PointingHandCursor)

        mini_layout = QVBoxLayout(self.mini_container)
        mini_layout.setContentsMargins(0, 0, 0, 0)
        mini_layout.setAlignment(Qt.AlignCenter)

        self.mini_bar = QFrame()
        self.mini_bar.setObjectName("mini_bar")
        self.mini_bar.setFixedSize(30, 5)
        self._update_mini_bar_style()

        mini_layout.addWidget(self.mini_bar)

        self.stack.addWidget(self.card)
        self.stack.addWidget(self.mini_container)

        self.main_layout.addWidget(self.stack)
        self.expand_window()

        self.is_host_muted = HostAudioController.get_host_mute()
        self._update_host_mute_ui()

    def on_fps_updated(self, fps: float):
        if self.stream_thread and self.stream_thread.isRunning() and not self.is_paused:
            self.fps_badge.setText(f"{fps:.0f} FPS")
            self.fps_badge.setVisible(True)
        else:
            self.fps_badge.setVisible(False)

    def _update_status_dot(self, color_hex: str):
        self.status_dot.setPixmap(svg_to_pixmap(SVG_STATUS_DOT, 14, 14, color_hex))

    def _populate_device_list(self):
        self.device_combo.blockSignals(True)
        self.device_combo.clear()

        devices = self.history_data.get("devices", {})
        last_ip = self.history_data.get("last_ip", "").strip()

        matched_index = -1
        idx = 0
        for ip, name in devices.items():
            label = f"{name} ({ip})" if name else ip
            dev_theme = get_device_theme(name or ip)
            item_icon = svg_to_icon(SVG_SCREEN, 14, dev_theme["accent"])

            self.device_combo.addItem(item_icon, label, ip)
            if ip == last_ip:
                matched_index = idx
            idx += 1

        if matched_index >= 0:
            self.device_combo.setCurrentIndex(matched_index)
            self.device_combo.setEditText(self.device_combo.itemText(matched_index))
        elif last_ip:
            self.device_combo.setEditText(last_ip)

        self.device_combo.blockSignals(False)

    def _on_device_index_changed(self, index: int):
        if index >= 0:
            target_ip = self.device_combo.itemData(index)
            item_text = self.device_combo.itemText(index)
            if target_ip:
                self.history_data["last_ip"] = str(target_ip).strip()
                self._schedule_history_save()
            self._update_device_combo_style(item_text or str(target_ip))

    def _on_device_text_changed(self, text: str):
        if text.strip():
            self._update_device_combo_style(text.strip())

    def get_selected_target_ip(self) -> str:
        raw_text = self.device_combo.currentText().strip()
        data_val = self.device_combo.currentData()
        cur_idx = self.device_combo.currentIndex()
        if data_val and str(data_val).strip():
            if cur_idx >= 0 and self.device_combo.itemText(cur_idx) == raw_text:
                return str(data_val).strip()

        if "(" in raw_text and ")" in raw_text:
            return raw_text.split("(")[-1].replace(")", "").strip()
        return raw_text

    def _update_mini_bar_style(self, override_color: Optional[str] = None):
        color = override_color or self.status_color
        alpha = self.current_opacity if self.is_mini_mode else 1.0
        self.mini_container.setStyleSheet(
            "QFrame#mini_container { background: transparent; }"
        )
        self.mini_bar.setStyleSheet(
            f"""
            QFrame#mini_bar {{
                background-color: {color};
                border: 1px solid rgba(255, 255, 255, {min(1.0, alpha * 0.7):.2f});
                border-radius: 2px;
            }}
            QFrame#mini_bar:hover {{
                background-color: #00a2ed;
                border: 1px solid #ffffff;
            }}
            """
        )

    def collapse_to_mini(self):
        self.is_mini_mode = True
        self.stack.setCurrentWidget(self.mini_container)
        self.apply_opacity(0.35)
        self.setFixedSize(48, 16)
        self.enforce_always_on_top()
        if sys.platform == "win32":
            exclude_from_capture(self)

    def expand_window(self):
        self.is_mini_mode = False
        if hasattr(self, "pulse_timer") and self.pulse_timer.isActive():
            self.pulse_timer.stop()
        self.setMinimumSize(0, 0)
        self.setMaximumSize(16777215, 16777215)
        self.stack.setCurrentWidget(self.card)
        op_val = self.opacity_slider.value() if hasattr(self, "opacity_slider") else 94
        self.apply_opacity(op_val / 100.0)
        self.card.adjustSize()
        self.adjustSize()
        self.enforce_always_on_top()
        if sys.platform == "win32":
            exclude_from_capture(self)

    def _get_tv_info_for_selection(self) -> tuple[str, list[str]]:
        current_ip = self.get_selected_target_ip()
        current_text = self.device_combo.currentText().strip()
        devices = self.history_data.get("devices", {})

        tv_name = ""
        ips = []

        if current_ip and current_ip in devices:
            tv_name = devices[current_ip]
        elif "(" in current_text:
            tv_name = current_text.split("(")[0].strip()

        if tv_name:
            for ip, name in devices.items():
                if name == tv_name and ip not in ips:
                    ips.append(ip)

        if current_ip and current_ip not in ips:
            ips.append(current_ip)

        return tv_name, ips

    def prompt_set_friendly_name(self):
        tv_name, ips = self._get_tv_info_for_selection()
        dlg = EditTvDialog(tv_name, ips, self)
        if dlg.exec() == QDialog.Accepted:
            new_name, new_ips = dlg.get_data()
            if not new_ips:
                return

            devices = self.history_data.get("devices", {})

            if tv_name:
                for old_ip in list(devices.keys()):
                    if devices[old_ip] == tv_name and old_ip not in new_ips:
                        del devices[old_ip]

            effective_name = new_name if new_name else (tv_name if tv_name else new_ips[0])
            for ip in new_ips:
                devices[ip] = effective_name

            self.history_data["devices"] = devices
            if new_ips:
                self.history_data["last_ip"] = new_ips[0]
            self._schedule_history_save()
            self._populate_device_list()

            for i in range(self.device_combo.count()):
                if self.device_combo.itemData(i) in new_ips:
                    self.device_combo.setCurrentIndex(i)
                    break

            self._update_device_combo_style(effective_name)

    def remove_selected_device(self):
        current_ip = self.get_selected_target_ip()
        devices = self.history_data.get("devices", {})
        if current_ip in devices:
            del devices[current_ip]
            self.history_data["devices"] = devices
            self._schedule_history_save()
            self._populate_device_list()
            self._update_device_combo_style(self.device_combo.currentText().strip())

    def ensure_control_channel(self, target_ip: str) -> bool:
        if not self.control_thread or not self.control_thread.isRunning():
            screen = self.screen() or QGuiApplication.primaryScreen()
            if screen:
                geom = screen.geometry()
                dpr = float(screen.devicePixelRatio())
                scr_w = max(1, int(round(geom.width() * dpr)))
                scr_h = max(1, int(round(geom.height() * dpr)))
                mon_l = int(round(geom.x() * dpr))
                mon_t = int(round(geom.y() * dpr))
            else:
                scr_w, scr_h, mon_l, mon_t = 1920, 1080, 0, 0

            self.control_thread = InputReceiverThread(
                target_ip,
                self.is_input_enabled,
                scr_w=scr_w,
                scr_h=scr_h,
                mon_l=mon_l,
                mon_t=mon_t,
            )
            self.control_thread.start()
            for _ in range(20):
                if self.control_thread.sock:
                    break
                time.sleep(0.05)
        return bool(self.control_thread and self.control_thread.isRunning())

    def send_receiver_window_command(self, action: str):
        target_ip = self.get_selected_target_ip() or self.discovered_ip
        if target_ip and self.ensure_control_channel(target_ip):
            self.control_thread.send_command(
                {"type": "window_control", "action": action}
            )

    def open_timer_dialog(self):
        dlg = TimerDialog(self)
        if dlg.exec() == QDialog.Accepted:
            args = dlg.get_args()
            if args:
                self.send_receiver_timer(args)

    def send_receiver_timer(self, args: str):
        target_ip = self.get_selected_target_ip() or self.discovered_ip
        clean_args = args.strip().strip('"').strip("'")
        if target_ip and self.ensure_control_channel(target_ip):
            self.control_thread.send_command({"type": "timer", "args": clean_args})

    def toggle_receiver_viewer(self):
        if self.viewer_window and self.viewer_window.isVisible():
            self.close_receiver_viewer()
        else:
            self.open_receiver_viewer()

    def open_receiver_viewer(self):
        target_ip = self.get_selected_target_ip() or self.discovered_ip
        if not target_ip:
            return

        self.ensure_control_channel(target_ip)

        if (
            self.stream_thread
            and self.stream_thread.isRunning()
            and not self.stream_thread.paused
        ):
            self.was_streaming_before_viewing = True
            self.stream_thread.pause_stream()
        else:
            self.was_streaming_before_viewing = False

        if self.control_thread:
            self.control_thread.send_command(
                {"type": "window_control", "action": "minimize"}
            )

        def send_remote_cmd(cmd: dict):
            if self.ensure_control_channel(target_ip):
                self.control_thread.send_command(cmd)

        if self.viewer_window:
            try:
                self.viewer_window.close()
            except Exception:
                pass

        self.viewer_window = RemoteReceiverViewerWindow(target_ip, send_remote_cmd)
        self.viewer_window.viewer_closed.connect(self._on_viewer_closed)
        self.viewer_window.show()

    def close_receiver_viewer(self):
        if self.viewer_window:
            try:
                self.viewer_window.close()
            except Exception:
                pass
            self.viewer_window = None

    def _on_viewer_closed(self):
        target_ip = self.get_selected_target_ip() or self.discovered_ip
        if target_ip and self.ensure_control_channel(target_ip):
            self.control_thread.send_command(
                {"type": "window_control", "action": "maximize"}
            )

        if self.was_streaming_before_viewing and self.stream_thread:
            self.stream_thread.resume_stream()
            self.is_paused = False
            self.pause_btn.setText("Pause")
            self.pause_btn.setIcon(svg_to_icon(SVG_PAUSE, 14, "#ffffff"))
            self.pause_btn.setStyleSheet(
                "background-color: #0078d4; color: white; font-weight: bold;"
            )
            self._update_status_color("#00d084")
            self._update_audio_pause_state()

        self.viewer_window = None

    def on_touch_input_toggled(self, checked: bool):
        self.input_enabled = checked
        self.history_data["touch_input"] = checked
        self._schedule_history_save()

    def on_auto_connect_toggled(self, checked: bool):
        self.history_data["auto_connect"] = checked
        self._schedule_history_save()

    def toggle_allow_audio_when_paused(self):
        self.allow_audio_when_paused = not self.allow_audio_when_paused
        self.history_data["allow_audio_when_paused"] = self.allow_audio_when_paused
        self._schedule_history_save()
        self._update_audio_pause_state()

    def on_allow_audio_when_paused_toggled(self, checked: bool):
        self.allow_audio_when_paused = checked
        self.history_data["allow_audio_when_paused"] = checked
        self._schedule_history_save()
        self._update_audio_pause_state()

    def _update_audio_pause_state(self):
        if self.audio_thread:
            should_pause_audio = self.is_paused and not self.allow_audio_when_paused
            self.audio_thread.paused = should_pause_audio

    def on_opacity_changed(self, val: int):
        self.op_val_lbl.setText(f"{val}%")
        if not self.is_mini_mode:
            self.apply_opacity(val / 100.0)
        self.history_data["opacity"] = val
        self._schedule_history_save()

    def is_input_enabled(self) -> bool:
        return self.input_enabled

    def eventFilter(self, watched, event):
        if hasattr(self, "device_combo") and (
            watched == self.device_combo or watched == self.device_combo.lineEdit()
        ):
            if event.type() == QEvent.KeyPress:
                key = event.key()
                if key in (Qt.Key_Up, Qt.Key_Down):
                    count = self.device_combo.count()
                    if count > 0:
                        view = self.device_combo.view()
                        if view and view.isVisible():
                            return False

                        cur = self.device_combo.currentIndex()
                        if cur < 0:
                            next_idx = 0 if key == Qt.Key_Down else (count - 1)
                        elif key == Qt.Key_Down:
                            next_idx = (cur + 1) if cur < count - 1 else 0
                        else:
                            next_idx = (cur - 1) if cur > 0 else (count - 1)

                        self.device_combo.setCurrentIndex(next_idx)
                        self.device_combo.lineEdit().setText(self.device_combo.itemText(next_idx))
                        self.device_combo.lineEdit().selectAll()
                        return True
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if not self.is_mini_mode:
                self.toggle_connect()
                event.accept()
                return
        super().keyPressEvent(event)

    def enterEvent(self, event):
        if self.is_any_popup_open():
            super().enterEvent(event)
            return
        if self.is_mini_mode and not self.is_pulsing:
            self.apply_opacity(1.0)
        self.enforce_always_on_top()
        super().enterEvent(event)

    def leaveEvent(self, event):
        if self.is_any_popup_open():
            super().leaveEvent(event)
            return
        if self.is_mini_mode and not self.is_pulsing:
            self.apply_opacity(0.35)
        self.enforce_always_on_top()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            if not self.is_any_popup_open():
                self.enforce_always_on_top()
            self._drag_start_pos = event.globalPosition().toPoint()
            self._window_start_pos = self.pos()
            self._is_dragging = False

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton:
            diff = event.globalPosition().toPoint() - self._drag_start_pos
            if diff.manhattanLength() > 2:
                self._is_dragging = True
                wh = self.windowHandle()
                if wh and hasattr(wh, "startSystemMove"):
                    if wh.startSystemMove():
                        return
                self.move(self._window_start_pos + diff)
                if self.is_mini_mode:
                    self.enforce_always_on_top()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            if self.is_mini_mode and not self._is_dragging:
                if event.modifiers() & Qt.ControlModifier:
                    self.toggle_pause()
                else:
                    self.expand_window()
            if not self.is_any_popup_open():
                self.enforce_always_on_top()

    def showEvent(self, event):
        self.enforce_always_on_top()
        if sys.platform == "win32":
            exclude_from_capture(self)
        super().showEvent(event)

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.setWindowFlags(menu.windowFlags() | Qt.WindowStaysOnTopHint | Qt.Popup)
        menu.setStyleSheet(
            """
            QMenu {
                background-color: #1a1e29;
                color: #ffffff;
                border: 1px solid #3d475f;
                border-radius: 8px;
                padding: 4px;
                font-family: 'Segoe UI', sans-serif;
                font-size: 13px;
            }
            QMenu::item {
                padding: 6px 20px 6px 12px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background-color: #0078d4;
                color: #ffffff;
            }
            QMenu::separator {
                height: 1px;
                background: #333c4d;
                margin: 4px 6px;
            }
            """
        )

        target_ip = self.get_selected_target_ip() or self.discovered_ip
        has_target = bool(target_ip)
        is_viewing = bool(self.viewer_window and self.viewer_window.isVisible())

        if is_viewing:
            view_rec_act = QAction("Cancel View and Control TV Screen", self)
            view_rec_act.setIcon(svg_to_icon(SVG_CLOSE, 16, "#ff6b6b"))
        else:
            view_rec_act = QAction("View and Control TV Screen", self)
            view_rec_act.setIcon(svg_to_icon(SVG_SCREEN, 16, "#00a2ed"))

        view_rec_act.triggered.connect(self.toggle_receiver_viewer)
        view_rec_act.setEnabled(has_target or is_viewing)
        menu.addAction(view_rec_act)

        menu.addSeparator()

        timer_act = QAction("TV Timer...", self)
        timer_act.setIcon(svg_to_icon(SVG_TIMER, 16, "#00d084"))
        timer_act.triggered.connect(self.open_timer_dialog)
        timer_act.setEnabled(has_target)
        menu.addAction(timer_act)

        menu.addSeparator()

        win_menu = menu.addMenu("TV Window Control")
        win_menu.setIcon(svg_to_icon(SVG_DISPLAY, 16, "#ffffff"))
        win_menu.setWindowFlags(win_menu.windowFlags() | Qt.WindowStaysOnTopHint | Qt.Popup)

        max_act = QAction("Maximize / Fullscreen TV", self)
        max_act.setIcon(svg_to_icon(SVG_MAXIMIZE, 16, "#ffffff"))
        max_act.triggered.connect(lambda: self.send_receiver_window_command("maximize"))
        win_menu.addAction(max_act)

        norm_act = QAction("Restore TV Window (Normal)", self)
        norm_act.setIcon(svg_to_icon(SVG_RESTORE, 16, "#ffffff"))
        norm_act.triggered.connect(lambda: self.send_receiver_window_command("normal"))
        win_menu.addAction(norm_act)

        min_act = QAction("Minimize TV Window", self)
        min_act.setIcon(svg_to_icon(SVG_MINIMIZE, 16, "#ffffff"))
        min_act.triggered.connect(lambda: self.send_receiver_window_command("minimize"))
        win_menu.addAction(min_act)
        win_menu.setEnabled(has_target)

        menu.addSeparator()

        rename_act = QAction("Set Friendly Name / TV IPs...", self)
        rename_act.setIcon(svg_to_icon(SVG_TAG, 16, "#ffffff"))
        rename_act.triggered.connect(self.prompt_set_friendly_name)
        menu.addAction(rename_act)

        remove_act = QAction("Remove Selected TV", self)
        remove_act.setIcon(svg_to_icon(SVG_TRASH, 16, "#ff6b6b"))
        remove_act.triggered.connect(self.remove_selected_device)
        menu.addAction(remove_act)

        menu.addSeparator()

        if self.allow_audio_when_paused:
            audio_pause_text = "Disable Audio When Paused"
            audio_pause_icon = svg_to_icon(SVG_VOLUME_ON, 16, "#00d084")
            audio_pause_tip = "Currently allowed: TV audio plays even while stream is paused"
        else:
            audio_pause_text = "Allow Audio When Paused"
            audio_pause_icon = svg_to_icon(SVG_VOLUME_MUTE, 16, "#8f9bb3")
            audio_pause_tip = "Currently muted: TV audio is silenced while stream is paused"

        allow_audio_pause_act = QAction(audio_pause_text, self)
        allow_audio_pause_act.setIcon(audio_pause_icon)
        allow_audio_pause_act.setToolTip(audio_pause_tip)
        allow_audio_pause_act.triggered.connect(self.toggle_allow_audio_when_paused)
        menu.addAction(allow_audio_pause_act)

        menu.addSeparator()

        if self.stream_thread and self.stream_thread.isRunning():
            pause_act = QAction(
                "Resume Stream" if self.is_paused else "Pause Stream", self
            )
            pause_act.setIcon(svg_to_icon(SVG_PLAY if self.is_paused else SVG_PAUSE, 16, "#ffffff"))
            pause_act.triggered.connect(self.toggle_pause)
            menu.addAction(pause_act)
            menu.addSeparator()

        if self.is_mini_mode:
            expand_act = QAction("Expand Controls", self)
            expand_act.setIcon(svg_to_icon(SVG_RESTORE, 16, "#ffffff"))
            expand_act.triggered.connect(self.expand_window)
            menu.addAction(expand_act)
        else:
            collapse_act = QAction("Collapse to Mini", self)
            collapse_act.setIcon(svg_to_icon(SVG_MINIMIZE, 16, "#ffffff"))
            collapse_act.triggered.connect(self.collapse_to_mini)
            menu.addAction(collapse_act)

        menu.addSeparator()
        quit_action = QAction("Exit MrCoopersScreenShare", self)
        quit_action.setIcon(svg_to_icon(SVG_LOGOUT, 16, "#ff6b6b"))
        quit_action.triggered.connect(self.close)
        menu.addAction(quit_action)

        win_geom = self.frameGeometry()
        screen = self.screen() or QGuiApplication.primaryScreen()
        screen_geom = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)

        menu_hint = menu.sizeHint()
        menu_w = max(240, menu_hint.width())
        menu_h = menu_hint.height()

        target_x = win_geom.right() + 6
        if target_x + menu_w > screen_geom.right():
            target_x = win_geom.left() - menu_w - 6

        if target_x < screen_geom.left():
            target_x = screen_geom.left() + 4

        target_y = win_geom.top()
        if target_y + menu_h > screen_geom.bottom():
            target_y = max(screen_geom.top() + 4, screen_geom.bottom() - menu_h - 6)
        if target_y < screen_geom.top():
            target_y = screen_geom.top() + 4

        target_pos = QPoint(target_x, target_y)

        self.topmost_timer.stop()

        if sys.platform == "win32":
            exclude_from_capture(menu)
            exclude_from_capture(win_menu)
            try:
                ctypes.windll.user32.SetWindowPos(
                    int(menu.winId()),
                    -1,
                    0, 0, 0, 0,
                    0x0002 | 0x0001 | 0x0040 | 0x0010,
                )
            except Exception:
                pass

        try:
            menu.exec(target_pos)
        finally:
            self.topmost_timer.start()
            self.enforce_always_on_top()

    def on_fps_changed(self, index: int):
        fps_map = {0: 60, 1: 30, 2: 120, 3: 15}
        chosen_fps = fps_map.get(index, 60)

        self.history_data["fps_preset"] = self.fps_combo.currentText()
        self._schedule_history_save()

        if self.stream_thread:
            self.stream_thread.set_fps_limit(chosen_fps)

    def _get_quality_settings(self, index: Optional[int] = None) -> tuple[int, bool, bool]:
        if index is None:
            index = self.quality_combo.currentIndex()
        text = self.quality_combo.itemText(index).lower()
        if "98%" in text:
            return 98, True, True
        elif "pixel-perfect" in text or "95%" in text:
            return 95, True, True
        elif "ultra" in text or "89%" in text or "92%" in text:
            return 89, False, False
        elif "high" in text or "82%" in text:
            return 82, False, False
        elif "balanced" in text or "72%" in text:
            return 72, False, False
        elif "studio" in text or "4:4:4" in text:
            return 88, True, True
        else:
            return 89, False, False

    def on_quality_changed(self, index: int):
        target_quality, use_444, native_res = self._get_quality_settings(index)

        self.history_data["quality_preset"] = self.quality_combo.currentText()
        self._schedule_history_save()

        if self.stream_thread:
            self.stream_thread.set_quality_params(target_quality, use_444, native_res)

    def on_tv_volume_changed(self, val: int):
        self.tv_vol_val_lbl.setText(f"{val}%")
        self.tv_volume = val / 100.0
        self.history_data["tv_volume"] = val
        self._schedule_history_save()
        if self.audio_thread:
            self.audio_thread.set_volume(self.tv_volume)

    def toggle_host_mute(self):
        new_state = not self.is_host_muted
        success = HostAudioController.set_host_mute(new_state)
        if success:
            self.is_host_muted = new_state
        else:
            self.is_host_muted = HostAudioController.get_host_mute()
        self._update_host_mute_ui()

    def _update_host_mute_ui(self):
        if self.is_host_muted:
            self.host_mute_btn.setText("Unmute Host")
            self.host_mute_btn.setIcon(svg_to_icon(SVG_VOLUME_ON, 14, "#ffffff"))
            self.host_mute_btn.setStyleSheet("background-color: #d83b01; color: white;")
        else:
            self.host_mute_btn.setText("Mute Host")
            self.host_mute_btn.setIcon(svg_to_icon(SVG_VOLUME_MUTE, 14, "#ffffff"))
            self.host_mute_btn.setStyleSheet("background-color: #262c3b; color: #ffffff;")

    def on_device_discovered(self, ip: str, pin_required: bool):
        self.discovered_ip = ip
        self.pin_required = pin_required
        print(f"[Discovery] Detected beacon from TV at {ip} (PIN required: {pin_required})", flush=True)

        if not self.device_combo.currentText().strip():
            self.device_combo.setEditText(ip)
            self._update_device_combo_style(ip)

        if (
            self.auto_connect_cb.isChecked()
            and (not self.stream_thread or not self.stream_thread.isRunning())
        ):
            if not pin_required or len(self.pin_input.text().strip()) == 4:
                print(f"[Discovery] Auto-connecting to {ip}...", flush=True)
                self.start_sharing()

    def on_pin_text_changed(self, text: str):
        self.history_data["pin"] = text.strip()
        self._schedule_history_save()

        if (
            len(text.strip()) == 4
            and self.auto_connect_cb.isChecked()
            and (not self.stream_thread or not self.stream_thread.isRunning())
        ):
            self.start_sharing()

    def toggle_connect(self):
        if self.stream_thread and self.stream_thread.isRunning():
            self.stop_sharing()
        else:
            self.start_sharing()

    def start_sharing(self):
        target_ip = self.get_selected_target_ip() or self.discovered_ip
        if not target_ip:
            print("[Sender-Main] Cannot start: No target IP specified.", flush=True)
            return

        fps_map = {0: 60, 1: 30, 2: 120, 3: 15}
        chosen_fps = fps_map.get(self.fps_combo.currentIndex(), 60)
        pin_code = self.pin_input.text().strip()

        target_quality, use_444, native_res = self._get_quality_settings()

        print(f"\n[Sender-Main] Launching screen share session to {target_ip} ({chosen_fps} FPS, Q={target_quality})...", flush=True)
        self.connect_btn.setText("Stop")
        self.connect_btn.setStyleSheet("background-color: #d83b01;")
        self.pause_btn.setEnabled(True)
        self.stream_mute_btn.setEnabled(True)

        self.stream_thread = ScreenSenderThread(
            target_ip=target_ip,
            pin=pin_code,
            quality=target_quality,
            fps_limit=chosen_fps,
            use_444_chroma=use_444,
            native_resolution=native_res,
        )
        self.stream_thread.status_changed.connect(self.on_stream_status)
        self.stream_thread.fps_updated.connect(self.on_fps_updated)
        self.stream_thread.start()

        self.audio_thread = AudioSenderThread(target_ip, volume=self.tv_volume)
        self._update_audio_pause_state()
        self.audio_thread.start()

        self.ensure_control_channel(target_ip)

    def stop_sharing(self):
        print("\n[Sender-Main] Stopping screen share session...", flush=True)
        if self.stream_thread:
            self.stream_thread.stop()
            self.stream_thread = None
        if self.audio_thread:
            self.audio_thread.stop()
            self.audio_thread = None

        self.is_paused = False
        self._update_audio_pause_state()
        self.fps_badge.setVisible(False)
        self.fps_badge.setText("")

        if self.viewer_window:
            try:
                self.viewer_window.close()
            except Exception:
                pass
            self.viewer_window = None

        if self.control_thread and not self.viewer_window:
            self.control_thread.stop()
            self.control_thread = None

        self.connect_btn.setText("Share")
        self.connect_btn.setStyleSheet("background-color: #0078d4;")
        self.pause_btn.setText("Pause")
        self.pause_btn.setIcon(svg_to_icon(SVG_PAUSE, 14, "#ffffff"))
        self.pause_btn.setStyleSheet("background-color: #0078d4; color: white;")
        self.pause_btn.setEnabled(False)
        self.stream_mute_btn.setEnabled(False)

        self._update_status_color("#8f9bb3")
        print("[Sender-Main] Session stopped.", flush=True)

    def toggle_pause(self):
        if self.stream_thread:
            if not self.is_paused:
                self.is_paused = True
                self.stream_thread.pause_stream()
                self.pause_btn.setText("Resume")
                self.pause_btn.setIcon(svg_to_icon(SVG_PLAY, 14, "#ffffff"))
                self.pause_btn.setStyleSheet(
                    "background-color: #f37021; color: white; font-weight: bold;"
                )
                self._update_status_color("#f37021")
                self.fps_badge.setVisible(False)
                print("[Sender-Main] Stream paused.", flush=True)
            else:
                self.is_paused = False
                self.stream_thread.resume_stream()
                self.pause_btn.setText("Pause")
                self.pause_btn.setIcon(svg_to_icon(SVG_PAUSE, 14, "#ffffff"))
                self.pause_btn.setStyleSheet(
                    "background-color: #0078d4; color: white; font-weight: bold;"
                )
                self._update_status_color("#00d084")
                print("[Sender-Main] Stream resumed.", flush=True)
            self._update_audio_pause_state()

    def toggle_stream_mute(self):
        if self.audio_thread:
            self.is_stream_muted = not self.is_stream_muted
            self.audio_thread.muted = self.is_stream_muted

            if self.is_stream_muted:
                self.stream_mute_btn.setText("TV Muted")
                self.stream_mute_btn.setIcon(svg_to_icon(SVG_VOLUME_MUTE, 14, "#ffffff"))
                self.stream_mute_btn.setStyleSheet(
                    "background-color: #d83b01; color: white; font-weight: bold;"
                )
                print("[Sender-Main] TV Audio stream muted.", flush=True)
            else:
                self.stream_mute_btn.setText("TV Audio")
                self.stream_mute_btn.setIcon(svg_to_icon(SVG_VOLUME_ON, 14, "#ffffff"))
                self.stream_mute_btn.setStyleSheet(
                    "background-color: #0078d4; color: white; font-weight: bold;"
                )
                print("[Sender-Main] TV Audio stream unmuted.", flush=True)

    def on_stream_status(self, text: str, active: bool):
        if active:
            color = "#f37021" if self.is_paused else "#00d084"
        else:
            color = "#d83b01"
            self.fps_badge.setVisible(False)
            self.fps_badge.setText("")
        self._update_status_color(color)

        if active:
            connected_ip = self.get_selected_target_ip() or self.discovered_ip
            if connected_ip:
                self.history_data["last_ip"] = connected_ip
                if connected_ip not in self.history_data.get("devices", {}):
                    self.history_data.setdefault("devices", {})[connected_ip] = ""
                self._schedule_history_save()
        else:
            if not self.viewer_window:
                self.stop_sharing()

    def _update_status_color(self, color_hex: str):
        self.status_color = color_hex
        self._update_status_dot(color_hex)
        if not self.is_pulsing:
            self._update_mini_bar_style()