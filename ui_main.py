"""
Main Floating Frameless Controller UI, Collapsed Mini Pill, Context Menu & Remote Timer Dialog.
"""

import ctypes
import math
import sys
import time
from typing import Optional

from PySide6.QtCore import QEvent, QPoint, Qt, QTimer
from PySide6.QtGui import QAction, QKeyEvent
from PySide6.QtWidgets import (
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
from utils import create_application_icon, load_history, save_history


class TimerDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Receiver Timer")
        self.setFixedWidth(340)
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

        title_lbl = QLabel("Set Receiver Timer")
        title_lbl.setStyleSheet("font-weight: bold; font-size: 13px; color: #00d084;")
        layout.addWidget(title_lbl)

        desc_lbl = QLabel("Enter duration (e.g. 30, 5m, 10:00, or raw number):")
        layout.addWidget(desc_lbl)

        input_layout = QHBoxLayout()
        self.timer_edit = QLineEdit()
        self.timer_edit.setPlaceholderText("30, 5m...")
        self.timer_edit.returnPressed.connect(self.accept)

        self.timer_btn = QPushButton("Start Timer")
        self.timer_btn.clicked.connect(self.accept)

        input_layout.addWidget(self.timer_edit)
        input_layout.addWidget(self.timer_btn)
        layout.addLayout(input_layout)

    def get_args(self) -> str:
        return self.timer_edit.text().strip().strip('"').strip("'")


class FloatingSenderWindow(QWidget):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("MrCoopersScreenShare - Sender")
        self.setWindowIcon(create_application_icon())

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
        self.input_enabled = True
        self.tv_volume = 1.0
        self.discovered_ip = ""
        self.pin_required = False
        self.status_color = "#8f9bb3"
        self.was_streaming_before_viewing = False

        # Pulsing Animation attributes for Collapsed Taskbar Click
        self.is_pulsing = False
        self.pulse_start_time = 0.0

        self.history_data = load_history()

        # Non-intrusive Topmost Enforcer Timer for Collapsed Mini Pill
        self.topmost_timer = QTimer(self)
        self.topmost_timer.setInterval(500)
        self.topmost_timer.timeout.connect(self._on_topmost_timer)
        self.topmost_timer.start()

        # 3-Second Red Pulse Timer for taskbar clicks in mini mode
        self.pulse_timer = QTimer(self)
        self.pulse_timer.setInterval(40)
        self.pulse_timer.timeout.connect(self._on_pulse_step)

        self._init_window()
        self._setup_ui()
        self._populate_device_list()

        # Start listening for auto-discovery beacon
        self.discovery_thread = DiscoveryListenerThread()
        self.discovery_thread.device_found.connect(self.on_device_discovered)
        self.discovery_thread.start()

    def _init_window(self):
        self.setWindowFlags(
            Qt.Window
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.WindowMinimizeButtonHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setWindowOpacity(0.94)

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
            except Exception as e:
                print(f"[DEBUG Sender Win32] Style init notice: {e}")

        self.enforce_always_on_top()

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
                SWP_NOMOVE = 0x0002
                SWP_NOSIZE = 0x0001
                SWP_NOACTIVATE = 0x0010
                SWP_SHOWWINDOW = 0x0040
                ctypes.windll.user32.SetWindowPos(
                    hwnd,
                    HWND_TOPMOST,
                    0,
                    0,
                    0,
                    0,
                    SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW,
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
            self.setWindowOpacity(0.35)
            self._update_mini_bar_style()
            return

        osc = (math.sin(elapsed * math.pi * 3.5) + 1.0) / 2.0
        current_op = 0.30 + (0.60 * osc)
        self.setWindowOpacity(current_op)
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
                    self.setWindowOpacity(self.opacity_slider.value() / 100.0)
        super().changeEvent(event)

    def _setup_ui(self):
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        self.stack = QStackedWidget(self)

        # View 1: Expanded Controller Card
        self.card = QFrame()
        self.card.setObjectName("card")
        self.card.setStyleSheet(
            """
            QFrame#card {
                background-color: #1a1e29;
                border: 1px solid #333c4d;
                border-radius: 14px;
            }
            QLabel { color: #ffffff; font-family: 'Segoe UI', sans-serif; font-size: 12px; }
            QLineEdit, QComboBox {
                background: #262c3b; border: 1px solid #3d475f;
                color: #ffffff; border-radius: 6px; padding: 5px 8px; font-size: 12px;
            }
            QPushButton {
                background-color: #0078d4; color: white; border: none;
                border-radius: 6px; font-weight: bold; font-size: 11px; padding: 6px 10px;
            }
            QPushButton:hover { background-color: #106ebe; }
            QCheckBox { color: #8f9bb3; font-size: 11px; }
            QSlider::groove:horizontal { height: 4px; background: #333c4e; border-radius: 2px; }
            QSlider::handle:horizontal { background: #00a2ed; width: 12px; margin: -4px 0; border-radius: 6px; }
        """
        )
        self.card_layout = QVBoxLayout(self.card)
        self.card_layout.setContentsMargins(12, 10, 12, 10)
        self.card_layout.setSpacing(8)

        # Header Bar
        self.header_bar = QWidget()
        h_layout = QHBoxLayout(self.header_bar)
        h_layout.setContentsMargins(0, 0, 0, 0)
        h_layout.setSpacing(6)

        self.status_dot = QLabel("●")
        self.status_dot.setStyleSheet(f"color: {self.status_color}; font-size: 14px;")
        self.title_lbl = QLabel("MrCoopersScreenShare")
        self.title_lbl.setStyleSheet("font-weight: bold; color: #00a2ed;")

        self.collapse_btn = QPushButton("▼")
        self.collapse_btn.setToolTip("Collapse to mini floating indicator")
        self.collapse_btn.setFixedSize(24, 24)
        self.collapse_btn.setStyleSheet(
            "background: #262c3b; border-radius: 12px; padding: 0px;"
        )
        self.collapse_btn.clicked.connect(self.collapse_to_mini)

        self.close_btn = QPushButton("✕")
        self.close_btn.setFixedSize(24, 24)
        self.close_btn.setStyleSheet(
            "background: #332228; color: #ff6b6b; border-radius: 12px; padding: 0px;"
        )
        self.close_btn.clicked.connect(self.close)

        h_layout.addWidget(self.status_dot)
        h_layout.addWidget(self.title_lbl)
        h_layout.addStretch()
        h_layout.addWidget(self.collapse_btn)
        h_layout.addWidget(self.close_btn)

        self.card_layout.addWidget(self.header_bar)

        # Row 1: Target Device Dropdown / IP Selector + Share Button
        ip_row = QHBoxLayout()
        self.device_combo = QComboBox()
        self.device_combo.setEditable(True)
        self.device_combo.setPlaceholderText("Select TV or Enter IP...")
        self.device_combo.setToolTip(
            "Select a TV by name or enter an IP. Right-click to assign friendly names."
        )
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
        self.fps_combo.setCurrentIndex(0)
        self.fps_combo.currentIndexChanged.connect(self.on_fps_changed)
        self.fps_combo.setToolTip("Framerate can be modified live at any time without stopping.")

        self.quality_combo = QComboBox()
        self.quality_combo.addItems(
            ["Ultra Crisp (98% 4:4:4)", "High Quality (90%)", "Balanced (75%)"]
        )
        self.quality_combo.setCurrentIndex(0)
        self.quality_combo.currentIndexChanged.connect(self.on_quality_changed)
        self.quality_combo.setToolTip(
            "Ultra Crisp preserves 4:4:4 full color resolution for razor-sharp text."
        )

        qual_row.addWidget(self.fps_combo)
        qual_row.addWidget(self.quality_combo)
        self.card_layout.addLayout(qual_row)

        # Row 3: Auto-connect & Touch Input toggles + Optional PIN input
        auto_row = QHBoxLayout()
        self.auto_connect_cb = QCheckBox("Auto-Connect")
        self.auto_connect_cb.setChecked(True)

        self.touch_input_cb = QCheckBox("TV Touch Control")
        self.touch_input_cb.setChecked(True)
        self.touch_input_cb.setToolTip(
            "When enabled, touching the TV screen controls this PC."
        )
        self.touch_input_cb.toggled.connect(self.on_touch_input_toggled)

        self.pin_input = QLineEdit()
        self.pin_input.setPlaceholderText("PIN (if req.)")
        self.pin_input.setMaxLength(4)
        self.pin_input.setFixedWidth(90)
        self.pin_input.returnPressed.connect(self.toggle_connect)
        self.pin_input.textChanged.connect(self.on_pin_text_changed)

        auto_row.addWidget(self.auto_connect_cb)
        auto_row.addWidget(self.touch_input_cb)
        auto_row.addStretch()
        auto_row.addWidget(self.pin_input)
        self.card_layout.addLayout(auto_row)

        # Row 4: Action Buttons (Pause/Resume, TV Audio Mute, Host Speaker Mute)
        btn_row = QHBoxLayout()
        self.pause_btn = QPushButton("⏸ Pause")
        self.pause_btn.clicked.connect(self.toggle_pause)
        self.pause_btn.setEnabled(False)

        self.stream_mute_btn = QPushButton("🔊 TV Audio")
        self.stream_mute_btn.clicked.connect(self.toggle_stream_mute)
        self.stream_mute_btn.setEnabled(False)

        self.host_mute_btn = QPushButton("🔇 Mute Host")
        self.host_mute_btn.setToolTip(
            "Mutes local PC speakers so audio only plays through the TV"
        )
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
        self.tv_vol_slider.setValue(100)
        self.tv_vol_slider.valueChanged.connect(self.on_tv_volume_changed)

        self.tv_vol_val_lbl = QLabel("100%")
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
        self.opacity_slider.setValue(94)
        self.opacity_slider.valueChanged.connect(
            lambda v: self.setWindowOpacity(v / 100.0) if not self.is_mini_mode else None
        )
        self.op_val_lbl = QLabel("94%")
        self.op_val_lbl.setFixedWidth(34)
        self.op_val_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.opacity_slider.valueChanged.connect(lambda v: self.op_val_lbl.setText(f"{v}%"))

        trans_row.addWidget(op_lbl_title)
        trans_row.addWidget(self.opacity_slider)
        trans_row.addWidget(self.op_val_lbl)
        self.card_layout.addLayout(trans_row)

        # View 2: Collapsed Mini Pill Indicator (48x16 Hitbox, 30x5 Bar)
        self.mini_container = QFrame()
        self.mini_container.setObjectName("mini_container")
        self.mini_container.setFixedSize(48, 16)
        self.mini_container.setCursor(Qt.PointingHandCursor)
        self.mini_container.setToolTip(
            "MrCoopersScreenShare (Ctrl+Click: Pause/Resume | Click: Expand | Drag: Move)"
        )

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

        if sys.platform == "win32":
            self.is_host_muted = HostAudioController.get_host_mute()
            self._update_host_mute_ui()

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
        self.mini_container.setStyleSheet(
            "QFrame#mini_container { background: transparent; }"
        )
        self.mini_bar.setStyleSheet(
            f"""
            QFrame#mini_bar {{
                background-color: {color};
                border: 1px solid rgba(255, 255, 255, 0.45);
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
        self.setWindowOpacity(0.35)
        self.setFixedSize(48, 16)
        self.enforce_always_on_top()

    def expand_window(self):
        self.is_mini_mode = False
        if hasattr(self, "pulse_timer") and self.pulse_timer.isActive():
            self.pulse_timer.stop()
        self.setMinimumSize(0, 0)
        self.setMaximumSize(16777215, 16777215)
        self.stack.setCurrentWidget(self.card)
        if hasattr(self, "opacity_slider"):
            self.setWindowOpacity(self.opacity_slider.value() / 100.0)
        self.card.adjustSize()
        self.adjustSize()
        self.enforce_always_on_top()

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
            save_history(self.history_data)
            self._populate_device_list()

    def remove_selected_device(self):
        current_ip = self.get_selected_target_ip()
        devices = self.history_data.get("devices", {})
        if current_ip in devices:
            del devices[current_ip]
            self.history_data["devices"] = devices
            save_history(self.history_data)
            self._populate_device_list()

    def ensure_control_channel(self, target_ip: str) -> bool:
        """Ensures a dedicated control channel is running to the target receiver."""
        if not self.control_thread or not self.control_thread.isRunning():
            self.control_thread = InputReceiverThread(target_ip, self.is_input_enabled)
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
        """Sends clean duration/argument string directly to receiver's linked timer."""
        target_ip = self.get_selected_target_ip() or self.discovered_ip
        clean_args = args.strip().strip('"').strip("'")
        if target_ip and self.ensure_control_channel(target_ip):
            self.control_thread.send_command({"type": "timer", "args": clean_args})

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
            self.stream_thread.paused = True
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

    def _on_viewer_closed(self):
        target_ip = self.get_selected_target_ip() or self.discovered_ip
        if target_ip and self.ensure_control_channel(target_ip):
            self.control_thread.send_command(
                {"type": "window_control", "action": "maximize"}
            )

        if self.was_streaming_before_viewing and self.stream_thread:
            self.stream_thread.paused = False

        self.viewer_window = None

    def on_touch_input_toggled(self, checked: bool):
        self.input_enabled = checked

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
            self.setWindowOpacity(1.0)
        self.enforce_always_on_top()
        super().enterEvent(event)

    def leaveEvent(self, event):
        if self.is_mini_mode and not self.is_pulsing:
            self.setWindowOpacity(0.35)
            self.enforce_always_on_top()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.enforce_always_on_top()
            self._drag_start_pos = event.globalPosition().toPoint()
            self._window_start_pos = self.pos()
            self._is_dragging = False

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton:
            diff = event.globalPosition().toPoint() - self._drag_start_pos
            if diff.manhattanLength() > 2:
                self._is_dragging = True
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

        target_ip = self.get_selected_target_ip() or self.discovered_ip
        has_target = bool(target_ip)

        view_rec_act = QAction("🖥️ View & Control TV Screen", self)
        view_rec_act.triggered.connect(self.open_receiver_viewer)
        view_rec_act.setEnabled(has_target)
        menu.addAction(view_rec_act)

        menu.addSeparator()

        timer_act = QAction("⏱️ TV Timer...", self)
        timer_act.triggered.connect(self.open_timer_dialog)
        timer_act.setEnabled(has_target)
        menu.addAction(timer_act)

        menu.addSeparator()

        win_menu = menu.addMenu("📺 TV Window Control")
        max_act = QAction("🗖 Maximize / Fullscreen TV", self)
        max_act.triggered.connect(lambda: self.send_receiver_window_command("maximize"))
        win_menu.addAction(max_act)

        norm_act = QAction("🗗 Restore TV Window (Normal)", self)
        norm_act.triggered.connect(lambda: self.send_receiver_window_command("normal"))
        win_menu.addAction(norm_act)

        min_act = QAction("🗕 Minimize TV Window", self)
        min_act.triggered.connect(lambda: self.send_receiver_window_command("minimize"))
        win_menu.addAction(min_act)
        win_menu.setEnabled(has_target)

        menu.addSeparator()

        rename_act = QAction("🏷️ Set Friendly Name for TV...", self)
        rename_act.triggered.connect(self.prompt_set_friendly_name)
        menu.addAction(rename_act)

        remove_act = QAction("🗑️ Remove Selected TV", self)
        remove_act.triggered.connect(self.remove_selected_device)
        menu.addAction(remove_act)

        menu.addSeparator()

        touch_act = QAction("Allow TV Touch Input", self)
        touch_act.setCheckable(True)
        touch_act.setChecked(self.input_enabled)
        touch_act.triggered.connect(lambda c: self.touch_input_cb.setChecked(c))
        menu.addAction(touch_act)

        menu.addSeparator()

        if self.stream_thread and self.stream_thread.isRunning():
            pause_act = QAction(
                "Resume Stream" if self.is_paused else "Pause Stream", self
            )
            pause_act.triggered.connect(self.toggle_pause)
            menu.addAction(pause_act)
            menu.addSeparator()

        if self.is_mini_mode:
            expand_act = QAction("Expand Controls", self)
            expand_act.triggered.connect(self.expand_window)
            menu.addAction(expand_act)
        else:
            collapse_act = QAction("Collapse to Mini", self)
            collapse_act.triggered.connect(self.collapse_to_mini)
            menu.addAction(collapse_act)

        menu.addSeparator()
        quit_action = QAction("Exit MrCoopersScreenShare", self)
        quit_action.triggered.connect(self.close)
        menu.addAction(quit_action)
        menu.exec(event.globalPos())

    def on_fps_changed(self, index: int):
        fps_map = {0: 60, 1: 30, 2: 120, 3: 15}
        chosen_fps = fps_map.get(index, 60)
        if self.stream_thread:
            self.stream_thread.set_fps_limit(chosen_fps)

    def on_quality_changed(self, index: int):
        if index == 0:
            target_quality, use_444 = 98, True
        elif index == 1:
            target_quality, use_444 = 90, True
        else:
            target_quality, use_444 = 75, False

        if self.stream_thread:
            self.stream_thread.set_quality_params(target_quality, use_444)

    def on_tv_volume_changed(self, val: int):
        self.tv_vol_val_lbl.setText(f"{val}%")
        self.tv_volume = val / 100.0
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
            self.host_mute_btn.setText("🔊 Unmute Host")
            self.host_mute_btn.setStyleSheet("background-color: #d83b01; color: white;")
        else:
            self.host_mute_btn.setText("🔇 Mute Host")
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

        quality_idx = self.quality_combo.currentIndex()
        if quality_idx == 0:
            target_quality, use_444 = 98, True
        elif quality_idx == 1:
            target_quality, use_444 = 90, True
        else:
            target_quality, use_444 = 75, False

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
        )
        self.stream_thread.status_changed.connect(self.on_stream_status)
        self.stream_thread.start()

        self.audio_thread = AudioSenderThread(target_ip, volume=self.tv_volume)
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
        self.pause_btn.setText("⏸ Pause")
        self.pause_btn.setStyleSheet("background-color: #0078d4; color: white;")
        self.pause_btn.setEnabled(False)
        self.stream_mute_btn.setEnabled(False)

        self._update_status_color("#8f9bb3")

    def toggle_pause(self):
        if self.stream_thread:
            if not self.is_paused:
                self.stream_thread.trigger_cursorless_frame()
                self.is_paused = True
                self.stream_thread.paused = True
                self.pause_btn.setText("▶ Resume")
                self.pause_btn.setStyleSheet(
                    "background-color: #f37021; color: white; font-weight: bold;"
                )
                self._update_status_color("#f37021")
            else:
                self.is_paused = False
                self.stream_thread.paused = False
                self.pause_btn.setText("⏸ Pause")
                self.pause_btn.setStyleSheet(
                    "background-color: #0078d4; color: white; font-weight: bold;"
                )
                self._update_status_color("#00d084")

    def toggle_stream_mute(self):
        if self.audio_thread:
            self.is_stream_muted = not self.is_stream_muted
            self.audio_thread.muted = self.is_stream_muted

            if self.is_stream_muted:
                self.stream_mute_btn.setText("🔇 TV Muted")
                self.stream_mute_btn.setStyleSheet(
                    "background-color: #d83b01; color: white; font-weight: bold;"
                )
            else:
                self.stream_mute_btn.setText("🔊 TV Audio")
                self.stream_mute_btn.setStyleSheet(
                    "background-color: #0078d4; color: white; font-weight: bold;"
                )

    def on_stream_status(self, text: str, active: bool):
        if active:
            color = "#f37021" if self.is_paused else "#00d084"
        else:
            color = "#d83b01"
        self._update_status_color(color)

        if active:
            connected_ip = self.get_selected_target_ip() or self.discovered_ip
            if connected_ip:
                self.history_data["last_ip"] = connected_ip
                if connected_ip not in self.history_data.get("devices", {}):
                    self.history_data.setdefault("devices", {})[connected_ip] = ""
                save_history(self.history_data)
        else:
            if not self.viewer_window:
                self.stop_sharing()

    def _update_status_color(self, color_hex: str):
        self.status_color = color_hex
        self.status_dot.setStyleSheet(f"color: {color_hex}; font-size: 14px;")
        if not self.is_pulsing:
            self._update_mini_bar_style()

    def closeEvent(self, event):
        self.stop_sharing()
        if self.control_thread:
            self.control_thread.stop()
            self.control_thread = None
        if self.discovery_thread:
            self.discovery_thread.stop()
        event.accept()