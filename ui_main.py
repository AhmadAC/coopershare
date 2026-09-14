# ui_main.py

"""
Main Floating Frameless Controller UI, Collapsed Mini Pill, Context Menu & Remote Timer Dialog.
Features persistent state loading and debounced saving to history.json (Quality preset, FPS, Volume, Opacity, PIN, etc.),
with vector SVG icons, dynamic audio-pause toggle feedback, full Linux Wayland/X11 move & opacity support,
cross-platform physical host mute control (Windows WASAPI & Linux PipeWire/WirePlumber),
toggleable Remote TV Viewer session controller, live 1-second GUI FPS counter, and Windows DWM capture exclusion.
"""

import ctypes
import math
import sys
import time
from typing import Optional

from PySide6.QtCore import QByteArray, QEvent, QPoint, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QGuiApplication, QKeyEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
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

        self.discovery_thread = DiscoveryListenerThread()
        self.discovery_thread.device_found.connect(self.on_device_discovered)
        self.discovery_thread.start()

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

    def _update_card_style(self):
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

        # GUI FPS Badge (updates every second with near-zero CPU footprint)
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
        self.device_combo = QComboBox()
        self.device_combo.setEditable(True)
        self.device_combo.setPlaceholderText("Select TV or Enter IP...")
        self.device_combo.lineEdit().returnPressed.connect(self.toggle_connect)

        self.connect_btn = QPushButton("Share")
        self.connect_btn.clicked.connect(self.toggle_connect)

        ip_row.addWidget(self.device_combo, 1)
        ip_row.addWidget(self.connect_btn)
        self.card_layout.addLayout(ip_row)

        # Row 2: Live-Adjustable FPS & Ultra-Quality Preset Selector
        qual_row = QHBoxLayout()
        self.fps_combo = QComboBox()
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

        self.quality_combo = QComboBox()
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

        # Query host physical mute state cross-platform (Windows & Linux PipeWire)
        self.is_host_muted = HostAudioController.get_host_mute()
        self._update_host_mute_ui()

    def on_fps_updated(self, fps: float):
        """Updates the GUI FPS badge once a second."""
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
            self.device_combo.addItem(label, ip)
            if ip == last_ip:
                matched_index = idx
            idx += 1

        if matched_index >= 0:
            self.device_combo.setCurrentIndex(matched_index)
        elif last_ip:
            self.device_combo.setEditText(last_ip)

        self.device_combo.blockSignals(False)

    def get_selected_target_ip(self) -> str:
        raw_text = self.device_combo.currentText().strip()
        data_val = self.device_combo.currentData()
        if data_val:
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

    def prompt_set_friendly_name(self):
        current_ip = self.get_selected_target_ip()
        if not current_ip:
            return

        devices = self.history_data.get("devices", {})
        existing_name = devices.get(current_ip, "")

        new_name, ok = QInputDialog.getText(
            self,
            "Set Friendly TV Name",
            f"Enter friendly name for display IP ({current_ip}):",
            QLineEdit.Normal,
            existing_name,
        )

        if ok and new_name.strip():
            devices[current_ip] = new_name.strip()
            self.history_data["devices"] = devices
            self.history_data["last_ip"] = current_ip
            self._schedule_history_save()
            self._populate_device_list()

    def remove_selected_device(self):
        current_ip = self.get_selected_target_ip()
        devices = self.history_data.get("devices", {})
        if current_ip in devices:
            del devices[current_ip]
            self.history_data["devices"] = devices
            self._schedule_history_save()
            self._populate_device_list()

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

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if not self.is_mini_mode:
                self.toggle_connect()
                event.accept()
                return
        super().keyPressEvent(event)

    def enterEvent(self, event):
        if self.is_mini_mode and not self.is_pulsing:
            self.apply_opacity(1.0)
        self.enforce_always_on_top()
        super().enterEvent(event)

    def leaveEvent(self, event):
        if self.is_mini_mode and not self.is_pulsing:
            self.apply_opacity(0.35)
        self.enforce_always_on_top()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
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
            self.enforce_always_on_top()

    def showEvent(self, event):
        self.enforce_always_on_top()
        if sys.platform == "win32":
            exclude_from_capture(self)
        super().showEvent(event)

    def contextMenuEvent(self, event):
        menu = QMenu(self)
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

        if sys.platform == "win32":
            menu.winId()
            exclude_from_capture(menu)

        target_ip = self.get_selected_target_ip() or self.discovered_ip
        has_target = bool(target_ip)
        is_viewing = bool(self.viewer_window and self.viewer_window.isVisible())

        if is_viewing:
            view_rec_act = QAction("Cancel View & Control TV Screen", self)
            view_rec_act.setIcon(svg_to_icon(SVG_CLOSE, 16, "#ff6b6b"))
        else:
            view_rec_act = QAction("View & Control TV Screen", self)
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

        if sys.platform == "win32":
            win_menu.winId()
            exclude_from_capture(win_menu)

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

        rename_act = QAction("Set Friendly Name for TV...", self)
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
        menu.exec(event.globalPos())

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

        if not self.device_combo.currentText().strip():
            self.device_combo.setEditText(ip)

        if (
            self.auto_connect_cb.isChecked()
            and (not self.stream_thread or not self.stream_thread.isRunning())
        ):
            if not pin_required or len(self.pin_input.text().strip()) == 4:
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
            return

        fps_map = {0: 60, 1: 30, 2: 120, 3: 15}
        chosen_fps = fps_map.get(self.fps_combo.currentIndex(), 60)
        pin_code = self.pin_input.text().strip()

        target_quality, use_444, native_res = self._get_quality_settings()

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
            else:
                self.is_paused = False
                self.stream_thread.resume_stream()
                self.pause_btn.setText("Pause")
                self.pause_btn.setIcon(svg_to_icon(SVG_PAUSE, 14, "#ffffff"))
                self.pause_btn.setStyleSheet(
                    "background-color: #0078d4; color: white; font-weight: bold;"
                )
                self._update_status_color("#00d084")
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
            else:
                self.stream_mute_btn.setText("TV Audio")
                self.stream_mute_btn.setIcon(svg_to_icon(SVG_VOLUME_ON, 14, "#ffffff"))
                self.stream_mute_btn.setStyleSheet(
                    "background-color: #0078d4; color: white; font-weight: bold;"
                )

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