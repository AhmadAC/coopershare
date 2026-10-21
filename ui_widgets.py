# ui_widgets.py

import ctypes
import re
import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from utils import SVG_TAG, SVG_TIMER, exclude_from_capture, svg_to_icon, svg_to_pixmap


class TimerDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Receiver Timer")
        self.setFixedWidth(360)
        self.setStyleSheet(
            """
            QDialog { background-color: #1a1e29; border: 1px solid #3d475f; border-radius: 10px; }
            QLabel { color: #ffffff; font-family: 'Segoe UI', sans-serif; font-size: 12px; }
            QLineEdit {
                background: #262c3b; border: 1px solid #3d475f; color: #ffffff;
                border-radius: 6px; padding: 6px 10px; font-size: 13px;
            }
            QPushButton {
                background-color: #0078d4; color: white; border: none;
                border-radius: 6px; font-weight: bold; font-size: 12px; padding: 7px 18px;
            }
            QPushButton:hover { background-color: #106ebe; }
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

        layout.addWidget(QLabel("Enter duration (e.g. 30, 5m, 10:00, or raw number):"))

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

    def showEvent(self, event):
        super().showEvent(event)
        if sys.platform == "win32":
            exclude_from_capture(self)

    def get_args(self) -> str:
        return self.timer_edit.text().strip().strip('"').strip("'")


class EditTvDialog(QDialog):
    def __init__(self, tv_name: str, ips: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Configure TV & Friendly Name")
        self.setFixedWidth(440)
        self.setStyleSheet(
            """
            QDialog { background-color: #1a1e29; border: 1px solid #3d475f; border-radius: 10px; }
            QLabel { color: #ffffff; font-family: 'Segoe UI', sans-serif; font-size: 12px; }
            QLineEdit {
                background: #262c3b; border: 1px solid #3d475f; color: #ffffff;
                border-radius: 6px; padding: 7px 10px; font-size: 13px;
            }
            QPushButton {
                background-color: #0078d4; color: white; border: none;
                border-radius: 6px; font-weight: bold; font-size: 12px; padding: 7px 18px;
            }
            QPushButton:hover { background-color: #106ebe; }
            QPushButton#cancel_btn { background-color: #262c3b; border: 1px solid #3d475f; }
            QPushButton#cancel_btn:hover { background-color: #333c4d; }
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

    def showEvent(self, event):
        super().showEvent(event)
        if sys.platform == "win32":
            exclude_from_capture(self)

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

        self.view().setStyleSheet(
            """
            QListView {
                background-color: #1a1e29; color: #ffffff; border: 1px solid #3d475f;
                border-radius: 6px; outline: none; padding: 4px; font-size: 12px;
            }
            QListView::item { min-height: 24px; padding: 4px 8px; border-radius: 4px; color: #ffffff; }
            QListView::item:selected, QListView::item:hover { background-color: #0078d4; color: #ffffff; }
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
                    ex_style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
                    ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex_style | 0x00000008)
                    ctypes.windll.user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0013)
                    ctypes.windll.user32.BringWindowToTop(hwnd)
                except Exception:
                    pass
            popup.raise_()

    def hidePopup(self):
        self._is_popup_open = False
        super().hidePopup()
        parent_win = self.window()
        if parent_win and hasattr(parent_win, "enforce_always_on_top"):
            QTimer.singleShot(100, parent_win.enforce_always_on_top)