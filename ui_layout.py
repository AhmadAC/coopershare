# ui_layout.py

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QSlider, QStackedWidget, QVBoxLayout, QWidget
)

from ui_theme import get_device_theme
from ui_widgets import TopmostComboBox
from utils import (
    SVG_CHEVRON_DOWN, SVG_CLOSE, SVG_EXTEND, SVG_PAUSE,
    SVG_STATUS_DOT, SVG_VOLUME_MUTE, SVG_VOLUME_ON,
    svg_to_icon, svg_to_pixmap
)


class SenderUIBuilder:
    """Builder class that handles UI initialization, component generation, and styling."""
    def __init__(self):
        self.device_combo = None
        self.fps_combo = None
        self.quality_combo = None
        self.auto_connect_cb = None
        self.touch_input_cb = None
        self.pin_input = None
        self.connect_btn = None
        self.pause_btn = None
        self.extend_btn = None
        self.stream_mute_btn = None
        self.host_mute_btn = None
        self.tv_vol_slider = None
        self.tv_vol_val_lbl = None
        self.opacity_slider = None
        self.op_val_lbl = None
        self.status_dot = None
        self.fps_badge = None
        self.card = None
        self.mini_container = None
        self.mini_bar = None
        self.stack = None

    def build_ui(self, window: QWidget, history: dict):
        main_layout = QVBoxLayout(window)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self.stack = QStackedWidget(window)
        self.card = QFrame()
        self.card.setObjectName("card")
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(14, 12, 14, 12)
        card_layout.setSpacing(8)

        # Header Bar
        header_bar = QWidget()
        header_bar.setFixedHeight(26)
        h_layout = QHBoxLayout(header_bar)
        h_layout.setContentsMargins(0, 0, 0, 0)
        h_layout.setSpacing(6)

        self.status_dot = QLabel()
        self.status_dot.setFixedSize(14, 14)
        self.status_dot.setPixmap(svg_to_pixmap(SVG_STATUS_DOT, 14, 14, "#8f9bb3"))

        title_lbl = QLabel("MrCoopersScreenShare")
        title_lbl.setStyleSheet("font-weight: bold; color: #00a2ed;")

        self.fps_badge = QLabel("")
        self.fps_badge.setVisible(False)
        self.fps_badge.setFixedHeight(18)
        self.fps_badge.setStyleSheet(
            "color: #00d084; font-weight: bold; font-size: 11px; padding: 1px 5px; "
            "background: rgba(0, 208, 132, 0.15); border: 1px solid rgba(0, 208, 132, 0.35); border-radius: 4px;"
        )

        self.collapse_btn = QPushButton()
        self.collapse_btn.setIcon(svg_to_icon(SVG_CHEVRON_DOWN, 12, "#8f9bb3"))
        self.collapse_btn.setIconSize(QSize(12, 12))
        self.collapse_btn.setFixedSize(24, 24)
        self.collapse_btn.setStyleSheet("background: #262c3b; border-radius: 12px; padding: 0px;")

        self.close_btn = QPushButton()
        self.close_btn.setIcon(svg_to_icon(SVG_CLOSE, 12, "#ff6b6b"))
        self.close_btn.setIconSize(QSize(12, 12))
        self.close_btn.setFixedSize(24, 24)
        self.close_btn.setStyleSheet("background: #332228; border-radius: 12px; padding: 0px;")

        h_layout.addWidget(self.status_dot, alignment=Qt.AlignVCenter)
        h_layout.addWidget(title_lbl, alignment=Qt.AlignVCenter)
        h_layout.addWidget(self.fps_badge, alignment=Qt.AlignVCenter)
        h_layout.addStretch()
        h_layout.addWidget(self.collapse_btn, alignment=Qt.AlignVCenter)
        h_layout.addWidget(self.close_btn, alignment=Qt.AlignVCenter)
        card_layout.addWidget(header_bar)

        # Row 1: Target Device Dropdown / IP Selector + Share Button
        ip_row = QHBoxLayout()
        self.device_combo = TopmostComboBox()
        self.device_combo.setEditable(True)
        self.device_combo.setFocusPolicy(Qt.StrongFocus)
        self.device_combo.setPlaceholderText("Select TV or Enter IP...")
        self.device_combo.lineEdit().setFocusPolicy(Qt.StrongFocus)

        self.connect_btn = QPushButton("Share")
        ip_row.addWidget(self.device_combo, 1)
        ip_row.addWidget(self.connect_btn)
        card_layout.addLayout(ip_row)

        # Row 2: Streaming Presets
        qual_row = QHBoxLayout()
        self.fps_combo = TopmostComboBox()
        self.fps_combo.addItems(["60 FPS (Ultra Smooth)", "30 FPS (Standard)"])
        saved_fps = history.get("fps_preset", "")
        self.fps_combo.setCurrentIndex(1 if "30" in str(saved_fps) else 0)

        self.quality_combo = TopmostComboBox()
        self.quality_combo.addItems([
            "Balanced HD (72%)", "High Quality HD (82%)",
            "Studio Crisp (88% 4:4:4)", "Pixel-Perfect 1:1 (95%)"
        ])
        saved_qual = str(history.get("quality_preset", "")).lower()
        idx = 3 if "95%" in saved_qual else 2 if "studio" in saved_qual else 1 if "high" in saved_qual else 0
        self.quality_combo.setCurrentIndex(idx)

        qual_row.addWidget(self.fps_combo)
        qual_row.addWidget(self.quality_combo)
        card_layout.addLayout(qual_row)

        # Row 3: Auto-connect & Touch Input toggles + PIN input
        auto_row = QHBoxLayout()
        self.auto_connect_cb = QCheckBox("Auto-Connect")
        self.auto_connect_cb.setChecked(bool(history.get("auto_connect", True)))

        self.touch_input_cb = QCheckBox("TV Touch Control")
        self.touch_input_cb.setChecked(bool(history.get("touch_input", True)))

        self.pin_input = QLineEdit()
        self.pin_input.setPlaceholderText("PIN (if req.)")
        self.pin_input.setMaxLength(4)
        self.pin_input.setFixedWidth(90)
        self.pin_input.setText(str(history.get("pin", "")))

        auto_row.addWidget(self.auto_connect_cb)
        auto_row.addWidget(self.touch_input_cb)
        auto_row.addStretch()
        auto_row.addWidget(self.pin_input)
        card_layout.addLayout(auto_row)

        # Row 4: Action Buttons
        btn_row = QHBoxLayout()
        self.pause_btn = QPushButton("Pause")
        self.pause_btn.setIcon(svg_to_icon(SVG_PAUSE, 14, "#ffffff"))
        self.pause_btn.setEnabled(False)

        self.extend_btn = QPushButton("Extend Screen")
        self.extend_btn.setIcon(svg_to_icon(SVG_EXTEND, 14, "#8f9bb3"))
        self.extend_btn.setStyleSheet("background-color: #262c3b; color: #ffffff;")

        self.stream_mute_btn = QPushButton("TV Audio")
        self.stream_mute_btn.setIcon(svg_to_icon(SVG_VOLUME_ON, 14, "#ffffff"))
        self.stream_mute_btn.setEnabled(False)

        self.host_mute_btn = QPushButton("Mute Host")
        self.host_mute_btn.setIcon(svg_to_icon(SVG_VOLUME_MUTE, 14, "#ffffff"))

        btn_row.addWidget(self.pause_btn)
        btn_row.addWidget(self.extend_btn)
        btn_row.addWidget(self.stream_mute_btn)
        btn_row.addWidget(self.host_mute_btn)
        card_layout.addLayout(btn_row)

        # Row 5: TV Volume Slider
        tv_vol_row = QHBoxLayout()
        tv_vol_lbl = QLabel("TV Vol:")
        tv_vol_lbl.setFixedWidth(46)
        self.tv_vol_slider = QSlider(Qt.Horizontal)
        self.tv_vol_slider.setRange(0, 150)
        vol_val = max(0, min(150, int(history.get("tv_volume", 100))))
        self.tv_vol_slider.setValue(vol_val)
        self.tv_vol_val_lbl = QLabel(f"{vol_val}%")
        self.tv_vol_val_lbl.setFixedWidth(34)
        self.tv_vol_val_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        tv_vol_row.addWidget(tv_vol_lbl)
        tv_vol_row.addWidget(self.tv_vol_slider)
        tv_vol_row.addWidget(self.tv_vol_val_lbl)
        card_layout.addLayout(tv_vol_row)

        # Row 6: Opacity Slider
        trans_row = QHBoxLayout()
        op_lbl = QLabel("Opacity:")
        op_lbl.setFixedWidth(46)
        self.opacity_slider = QSlider(Qt.Horizontal)
        self.opacity_slider.setRange(20, 100)
        op_val = max(20, min(100, int(history.get("opacity", 94))))
        self.opacity_slider.setValue(op_val)
        self.op_val_lbl = QLabel(f"{op_val}%")
        self.op_val_lbl.setFixedWidth(34)
        self.op_val_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        trans_row.addWidget(op_lbl)
        trans_row.addWidget(self.opacity_slider)
        trans_row.addWidget(self.op_val_lbl)
        card_layout.addLayout(trans_row)

        # Collapsed Mini Pill Indicator
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
        mini_layout.addWidget(self.mini_bar)

        self.stack.addWidget(self.card)
        self.stack.addWidget(self.mini_container)
        main_layout.addWidget(self.stack)
        self.update_card_style(op_val / 100.0, False)

    def update_card_style(self, opacity: float, is_mini: bool):
        alpha = opacity if not is_mini else 1.0
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
            QComboBox::drop-down {{ width: 22px; border-left-width: 0px; }}
            QComboBox::down-arrow {{
                width: 0px; height: 0px; border-left: 4px solid transparent;
                border-right: 4px solid transparent; border-top: 5px solid #8f9bb3; margin-right: 6px;
            }}
            QComboBox::down-arrow:hover {{ border-top: 5px solid #ffffff; }}
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

    def update_device_combo_style(self, device_text: str):
        theme = get_device_theme(device_text)
        accent, bg, border = theme["accent"], theme["bg"], theme["border"]
        self.device_combo.setStyleSheet(
            f"""
            QComboBox {{
                background-color: {bg}; border: 1.5px solid {border}; color: #ffffff;
                border-radius: 6px; padding: 5px 8px; font-size: 12px; font-weight: bold;
            }}
            QComboBox:hover, QComboBox:focus {{ border: 1.5px solid {accent}; }}
            QComboBox QLineEdit {{ background: transparent; border: none; color: #ffffff; font-size: 12px; font-weight: bold; padding: 0px; }}
            QComboBox::drop-down {{ width: 22px; border-left-width: 0px; }}
            QComboBox::down-arrow {{
                width: 0px; height: 0px; border-left: 4px solid transparent;
                border-right: 4px solid transparent; border-top: 5px solid {accent}; margin-right: 6px;
            }}
            QComboBox::down-arrow:hover {{ border-top: 5px solid #ffffff; }}
            """
        )

    def update_mini_bar_style(self, color: str, opacity: float, is_mini: bool):
        alpha = opacity if is_mini else 1.0
        self.mini_container.setStyleSheet("QFrame#mini_container { background: transparent; }")
        self.mini_bar.setStyleSheet(
            f"""
            QFrame#mini_bar {{
                background-color: {color}; border: 1px solid rgba(255, 255, 255, {min(1.0, alpha * 0.7):.2f}); border-radius: 2px;
            }}
            QFrame#mini_bar:hover {{ background-color: #00a2ed; border: 1px solid #ffffff; }}
            """
        )