# ui_main.py

"""
MrCoopersScreenShare - Sender (Facade Layer)
Manages Window behavior, History configurations, Context Menus, and glues the UI to the Stream Controller.
"""

import sys
from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QGuiApplication, QKeyEvent
from PySide6.QtWidgets import QMenu, QWidget

from session_manager import SessionManager
from ui_layout import SenderUIBuilder
from ui_widgets import EditTvDialog, TimerDialog
from utils import (
    SVG_CLOSE, SVG_DISPLAY, SVG_EXTEND, SVG_LOGOUT, SVG_MAXIMIZE,
    SVG_MINIMIZE, SVG_PAUSE, SVG_PLAY, SVG_RESTORE, SVG_SCREEN,
    SVG_TAG, SVG_TIMER, SVG_TRASH, SVG_VOLUME_MUTE, SVG_VOLUME_ON,
    create_application_icon, ensure_kwin_rules, exclude_from_capture,
    load_history, raise_wayland_topmost, save_history, svg_to_icon, svg_to_pixmap
)


class FloatingSenderWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MrCoopersScreenShare - Sender")
        self.setWindowIcon(create_application_icon())

        self.history = load_history()
        self.session = SessionManager()
        self.ui = SenderUIBuilder()

        self._drag_start_pos = QPoint()
        self._window_start_pos = QPoint()
        self._is_dragging = False

        self.is_mini_mode = False
        self.discovered_ip = ""
        self._expanded_size = QSize(420, 310)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(400)
        self._save_timer.timeout.connect(lambda: save_history(self.history))

        self.topmost_timer = QTimer(self)
        self.topmost_timer.setInterval(1000)
        self.topmost_timer.timeout.connect(self._on_topmost_timer)

        self.pulse_timer = QTimer(self)
        self.pulse_timer.setInterval(40)
        self.pulse_timer.timeout.connect(self._on_pulse_step)

        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        self.ui.build_ui(self, self.history)
        self._init_session_state()
        self._connect_signals()
        self.topmost_timer.start()

    def _init_session_state(self):
        self.session.share_extension = bool(self.history.get("share_extension", False))
        self.session.input_enabled = bool(self.history.get("touch_input", True))
        self.session.allow_audio_when_paused = bool(self.history.get("allow_audio_when_paused", False))
        self.session.tv_volume = max(0, min(150, int(self.history.get("tv_volume", 100)))) / 100.0

        if self.session.share_extension:
            self.ui.extend_btn.setText("Screen Ext.")
            self.ui.extend_btn.setIcon(svg_to_icon(SVG_EXTEND, 14, "#ffffff"))
            self.ui.extend_btn.setStyleSheet("background-color: #0078d4; color: #ffffff; font-weight: bold;")

        if self.session.is_host_muted:
            self.ui.host_mute_btn.setText("Unmute Host")
            self.ui.host_mute_btn.setIcon(svg_to_icon(SVG_VOLUME_ON, 14, "#ffffff"))
            self.ui.host_mute_btn.setStyleSheet("background-color: #d83b01; color: white;")

        self._populate_device_list()
        self.ui.update_device_combo_style(self.get_selected_ip())

    def _connect_signals(self):
        self.ui.device_combo.currentIndexChanged.connect(self._on_device_changed)
        self.ui.device_combo.editTextChanged.connect(lambda t: self.ui.update_device_combo_style(t.strip()))
        self.ui.device_combo.installEventFilter(self)
        self.ui.device_combo.lineEdit().installEventFilter(self)

        self.ui.connect_btn.clicked.connect(self.toggle_connect)
        self.ui.fps_combo.currentIndexChanged.connect(self._on_fps_changed)
        self.ui.quality_combo.currentIndexChanged.connect(self._on_quality_changed)
        self.ui.auto_connect_cb.toggled.connect(lambda c: self._update_history("auto_connect", c))
        self.ui.touch_input_cb.toggled.connect(self._on_touch_toggled)
        self.ui.pin_input.textChanged.connect(self._on_pin_changed)
        self.ui.pause_btn.clicked.connect(self.toggle_pause)
        self.ui.extend_btn.clicked.connect(self.toggle_extension)
        self.ui.stream_mute_btn.clicked.connect(self.toggle_stream_mute)
        self.ui.host_mute_btn.clicked.connect(self.toggle_host_mute)
        self.ui.tv_vol_slider.valueChanged.connect(self._on_volume_changed)
        self.ui.opacity_slider.valueChanged.connect(self._on_opacity_changed)

        self.session.status_changed.connect(self.on_stream_status)
        self.session.fps_updated.connect(self._on_fps_updated)
        self.session.device_discovered.connect(self.on_device_discovered)

    def _update_history(self, key, val):
        self.history[key] = val
        self._save_timer.start()

    def enforce_always_on_top(self):
        if not self.isVisible() or self.ui.device_combo._is_popup_open: return
        import ctypes
        if sys.platform == "win32":
            try:
                hwnd = int(self.winId())
                ex = ctypes.windll.user32.GetWindowLongW(hwnd, -20) | 0x00000008
                if self.is_mini_mode: ex |= 0x00000080
                ctypes.windll.user32.SetWindowLongW(hwnd, -20, ex)
                ctypes.windll.user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0053)
                ctypes.windll.user32.BringWindowToTop(hwnd)
            except Exception: pass
            self.raise_()
        else:
            raise_wayland_topmost(self)

    def _on_topmost_timer(self):
        if not self.ui.device_combo._is_popup_open and self.is_mini_mode and not self.isMinimized():
            self.enforce_always_on_top()

    def get_selected_ip(self) -> str:
        txt = self.ui.device_combo.currentText().strip()
        data = self.ui.device_combo.currentData()
        idx = self.ui.device_combo.currentIndex()
        if data and str(data).strip() and idx >= 0 and self.ui.device_combo.itemText(idx) == txt:
            return str(data).strip()
        return txt.split("(")[-1].replace(")", "").strip() if "(" in txt else txt

    def _populate_device_list(self):
        self.ui.device_combo.blockSignals(True)
        self.ui.device_combo.clear()
        devices = self.history.get("devices", {})
        last_ip = self.history.get("last_ip", "").strip()

        matched = -1
        for i, (ip, name) in enumerate(devices.items()):
            label = f"{name} ({ip})" if name else ip
            icon = svg_to_icon(SVG_SCREEN, 14, "#00b4d8")
            self.ui.device_combo.addItem(icon, label, ip)
            if ip == last_ip: matched = i

        if matched >= 0:
            self.ui.device_combo.setCurrentIndex(matched)
            self.ui.device_combo.setEditText(self.ui.device_combo.itemText(matched))
        elif last_ip:
            self.ui.device_combo.setEditText(last_ip)
        self.ui.device_combo.blockSignals(False)

    def _on_device_changed(self, idx):
        if idx >= 0:
            ip = self.ui.device_combo.itemData(idx)
            if ip: self._update_history("last_ip", str(ip).strip())
            self.ui.update_device_combo_style(self.ui.device_combo.itemText(idx) or str(ip))

    def _on_fps_changed(self, idx):
        self._update_history("fps_preset", self.ui.fps_combo.currentText())
        if self.session.stream_thread:
            self.session.stream_thread.set_fps_limit(60 if idx == 0 else 30)

    def _on_quality_changed(self, idx):
        self._update_history("quality_preset", self.ui.quality_combo.currentText())
        txt = self.ui.quality_combo.itemText(idx).lower()
        use_444 = "95%" in txt or "studio" in txt
        native = "95%" in txt
        qual = 95 if "95%" in txt else 88 if "studio" in txt else 82 if "high" in txt else 72
        if self.session.stream_thread:
            self.session.stream_thread.set_quality_params(qual, use_444, native)

    def _on_touch_toggled(self, checked):
        self.session.input_enabled = checked
        self._update_history("touch_input", checked)

    def _on_pin_changed(self, txt):
        self._update_history("pin", txt.strip())
        if len(txt.strip()) == 4 and self.ui.auto_connect_cb.isChecked() and not self.session.stream_thread:
            self.toggle_connect()

    def _on_volume_changed(self, val):
        self.ui.tv_vol_val_lbl.setText(f"{val}%")
        self.session.tv_volume = val / 100.0
        self._update_history("tv_volume", val)
        if self.session.audio_thread:
            self.session.audio_thread.set_volume(self.session.tv_volume)

    def _on_opacity_changed(self, val):
        self.ui.op_val_lbl.setText(f"{val}%")
        self._update_history("opacity", val)
        if not self.is_mini_mode:
            self.ui.update_card_style(val / 100.0, False)
            if sys.platform != "win32": self.setWindowOpacity(val / 100.0)

    def on_device_discovered(self, ip, pin_req):
        self.discovered_ip = ip
        if not self.ui.device_combo.currentText().strip():
            self.ui.device_combo.setEditText(ip)
            self.ui.update_device_combo_style(ip)
        if self.ui.auto_connect_cb.isChecked() and not self.session.stream_thread:
            if not pin_req or len(self.ui.pin_input.text().strip()) == 4:
                self.toggle_connect()

    def toggle_connect(self):
        if self.session.stream_thread:
            self.session.stop_sharing()
        else:
            ip = self.get_selected_ip() or self.discovered_ip
            if not ip: return
            fps = 60 if self.ui.fps_combo.currentIndex() == 0 else 30
            txt = self.ui.quality_combo.currentText().lower()
            use_444, native = "95%" in txt or "studio" in txt, "95%" in txt
            qual = 95 if "95%" in txt else 88 if "studio" in txt else 82 if "high" in txt else 72

            self.ui.connect_btn.setText("Stop")
            self.ui.connect_btn.setStyleSheet("background-color: #d83b01;")
            self.ui.pause_btn.setEnabled(True)
            self.ui.stream_mute_btn.setEnabled(True)
            self.session.start_sharing(ip, fps, self.ui.pin_input.text().strip(), qual, use_444, native)

    def toggle_pause(self):
        if self.session.toggle_pause():
            self.ui.pause_btn.setText("Resume")
            self.ui.pause_btn.setIcon(svg_to_icon(SVG_PLAY, 14, "#ffffff"))
            self.ui.pause_btn.setStyleSheet("background-color: #f37021; color: white; font-weight: bold;")
            self.ui.fps_badge.setVisible(False)
        else:
            self.ui.pause_btn.setText("Pause")
            self.ui.pause_btn.setIcon(svg_to_icon(SVG_PAUSE, 14, "#ffffff"))
            self.ui.pause_btn.setStyleSheet("background-color: #0078d4; color: white; font-weight: bold;")

    def toggle_extension(self):
        is_ext = self.session.toggle_share_extension()
        self._update_history("share_extension", is_ext)
        if is_ext:
            self.ui.extend_btn.setText("Screen Ext.")
            self.ui.extend_btn.setIcon(svg_to_icon(SVG_EXTEND, 14, "#ffffff"))
            self.ui.extend_btn.setStyleSheet("background-color: #0078d4; color: #ffffff; font-weight: bold;")
        else:
            self.ui.extend_btn.setText("Extend Screen")
            self.ui.extend_btn.setIcon(svg_to_icon(SVG_EXTEND, 14, "#8f9bb3"))
            self.ui.extend_btn.setStyleSheet("background-color: #262c3b; color: #ffffff;")

    def toggle_stream_mute(self):
        if self.session.toggle_stream_mute():
            self.ui.stream_mute_btn.setText("TV Muted")
            self.ui.stream_mute_btn.setIcon(svg_to_icon(SVG_VOLUME_MUTE, 14, "#ffffff"))
            self.ui.stream_mute_btn.setStyleSheet("background-color: #d83b01; color: white; font-weight: bold;")
        else:
            self.ui.stream_mute_btn.setText("TV Audio")
            self.ui.stream_mute_btn.setIcon(svg_to_icon(SVG_VOLUME_ON, 14, "#ffffff"))
            self.ui.stream_mute_btn.setStyleSheet("background-color: #0078d4; color: white; font-weight: bold;")

    def toggle_host_mute(self):
        if self.session.toggle_host_mute():
            self.ui.host_mute_btn.setText("Unmute Host")
            self.ui.host_mute_btn.setIcon(svg_to_icon(SVG_VOLUME_ON, 14, "#ffffff"))
            self.ui.host_mute_btn.setStyleSheet("background-color: #d83b01; color: white;")
        else:
            self.ui.host_mute_btn.setText("Mute Host")
            self.ui.host_mute_btn.setIcon(svg_to_icon(SVG_VOLUME_MUTE, 14, "#ffffff"))
            self.ui.host_mute_btn.setStyleSheet("background-color: #262c3b; color: #ffffff;")

    def on_stream_status(self, txt, active, color):
        self.ui.status_dot.setPixmap(svg_to_pixmap(SVG_STATUS_DOT, 14, 14, color))
        self.ui.update_mini_bar_style(color, self.ui.opacity_slider.value() / 100.0, self.is_mini_mode)
        if active:
            ip = self.get_selected_ip() or self.discovered_ip
            if ip:
                self._update_history("last_ip", ip)
                if ip not in self.history.get("devices", {}):
                    self.history.setdefault("devices", {})[ip] = ""
                    self._save_timer.start()
        else:
            self.ui.fps_badge.setVisible(False)
            self.ui.connect_btn.setText("Share")
            self.ui.connect_btn.setStyleSheet("background-color: #0078d4;")
            self.ui.pause_btn.setText("Pause")
            self.ui.pause_btn.setIcon(svg_to_icon(SVG_PAUSE, 14, "#ffffff"))
            self.ui.pause_btn.setStyleSheet("background-color: #0078d4; color: white;")
            self.ui.pause_btn.setEnabled(False)
            self.ui.stream_mute_btn.setEnabled(False)

    def _on_fps_updated(self, fps):
        if self.session.stream_thread and not self.session.is_paused:
            self.ui.fps_badge.setText(f"{fps:.0f} FPS")
            self.ui.fps_badge.setVisible(True)

    def eventFilter(self, w, event):
        if w in (self.ui.device_combo, self.ui.device_combo.lineEdit()) and event.type() == QEvent.KeyPress:
            k = event.key()
            if k in (Qt.Key_Up, Qt.Key_Down) and self.ui.device_combo.count() > 0:
                if self.ui.device_combo.view() and self.ui.device_combo.view().isVisible(): return False
                c = self.ui.device_combo.currentIndex()
                nx = (c + 1 if c < self.ui.device_combo.count() - 1 else 0) if k == Qt.Key_Down else (c - 1 if c > 0 else self.ui.device_combo.count() - 1)
                self.ui.device_combo.setCurrentIndex(nx)
                self.ui.device_combo.lineEdit().setText(self.ui.device_combo.itemText(nx))
                return True
        return super().eventFilter(w, event)

    def contextMenuEvent(self, event):
        m = QMenu(self)
        m.setWindowFlags(m.windowFlags() | Qt.WindowStaysOnTopHint | Qt.Popup)
        m.setStyleSheet(
            "QMenu { background-color: #1a1e29; color: #ffffff; border: 1px solid #3d475f; border-radius: 8px; padding: 4px; font-size: 13px; }"
            "QMenu::item { padding: 6px 20px 6px 12px; border-radius: 4px; }"
            "QMenu::item:selected { background-color: #0078d4; }"
            "QMenu::separator { height: 1px; background: #333c4d; margin: 4px 6px; }"
        )

        ip = self.get_selected_ip() or self.discovered_ip
        is_view = bool(self.session.viewer_window)

        v_act = QAction("Cancel View" if is_view else "View TV Screen", self)
        v_act.setIcon(svg_to_icon(SVG_CLOSE if is_view else SVG_SCREEN, 16, "#ff6b6b" if is_view else "#00a2ed"))
        v_act.triggered.connect(lambda: self.session.close_viewer() if is_view else self.session.open_viewer(ip))
        v_act.setEnabled(bool(ip) or is_view)
        m.addAction(v_act)
        m.addSeparator()

        e_act = QAction("Share Screen Extension (Display 2)", self)
        e_act.setIcon(svg_to_icon(SVG_EXTEND, 16, "#00a2ed" if self.session.share_extension else "#ffffff"))
        e_act.setCheckable(True)
        e_act.setChecked(self.session.share_extension)
        e_act.triggered.connect(self.toggle_extension)
        m.addAction(e_act)

        t_act = QAction("TV Timer...", self)
        t_act.setIcon(svg_to_icon(SVG_TIMER, 16, "#00d084"))
        t_act.triggered.connect(lambda: self._show_timer_dlg(ip))
        t_act.setEnabled(bool(ip))
        m.addAction(t_act)
        m.addSeparator()

        wm = m.addMenu("TV Window Control")
        wm.setIcon(svg_to_icon(SVG_DISPLAY, 16, "#ffffff"))
        wm.setWindowFlags(wm.windowFlags() | Qt.WindowStaysOnTopHint | Qt.Popup)
        for t, ic, a in [("Maximize", SVG_MAXIMIZE, "maximize"), ("Restore", SVG_RESTORE, "normal"), ("Minimize", SVG_MINIMIZE, "minimize")]:
            act = QAction(t, self)
            act.setIcon(svg_to_icon(ic, 16, "#ffffff"))
            act.triggered.connect(lambda checked, ax=a: self.session.send_window_command(ip, ax))
            wm.addAction(act)
        wm.setEnabled(bool(ip))
        m.addSeparator()

        r_act = QAction("Set Friendly Name...", self)
        r_act.setIcon(svg_to_icon(SVG_TAG, 16, "#ffffff"))
        r_act.triggered.connect(self._show_rename_dlg)
        m.addAction(r_act)

        rm_act = QAction("Remove TV", self)
        rm_act.setIcon(svg_to_icon(SVG_TRASH, 16, "#ff6b6b"))
        rm_act.triggered.connect(self._remove_tv)
        m.addAction(rm_act)
        m.addSeparator()

        ap_act = QAction("Disable Audio When Paused" if self.session.allow_audio_when_paused else "Allow Audio When Paused", self)
        ap_act.setIcon(svg_to_icon(SVG_VOLUME_ON if self.session.allow_audio_when_paused else SVG_VOLUME_MUTE, 16, "#00d084" if self.session.allow_audio_when_paused else "#8f9bb3"))
        ap_act.triggered.connect(self._toggle_audio_paused)
        m.addAction(ap_act)
        m.addSeparator()

        if self.is_mini_mode:
            ex_act = QAction("Expand Controls", self)
            ex_act.setIcon(svg_to_icon(SVG_RESTORE, 16, "#ffffff"))
            ex_act.triggered.connect(self.expand_window)
            m.addAction(ex_act)
        else:
            col_act = QAction("Collapse to Mini", self)
            col_act.setIcon(svg_to_icon(SVG_MINIMIZE, 16, "#ffffff"))
            col_act.triggered.connect(self.collapse_to_mini)
            m.addAction(col_act)
        m.addSeparator()

        q_act = QAction("Exit", self)
        q_act.setIcon(svg_to_icon(SVG_LOGOUT, 16, "#ff6b6b"))
        q_act.triggered.connect(self.close)
        m.addAction(q_act)

        self.topmost_timer.stop()
        if sys.platform == "win32":
            exclude_from_capture(m); exclude_from_capture(wm)
            try: ctypes.windll.user32.SetWindowPos(int(m.winId()), -1, 0, 0, 0, 0, 0x0053)
            except Exception: pass
        try: m.exec(QPoint(self.frameGeometry().right() + 6, self.frameGeometry().top()))
        finally:
            self.topmost_timer.start()
            self.enforce_always_on_top()

    def _show_timer_dlg(self, ip):
        dlg = TimerDialog(self)
        if dlg.exec() == QDialog.Accepted and dlg.get_args():
            self.session.send_timer(ip, dlg.get_args())

    def _show_rename_dlg(self):
        ip = self.get_selected_ip()
        txt = self.ui.device_combo.currentText().strip()
        devs = self.history.get("devices", {})
        name = devs.get(ip, txt.split("(")[0].strip() if "(" in txt else "")
        ips = [i for i, n in devs.items() if n == name and name]
        if ip and ip not in ips: ips.append(ip)

        dlg = EditTvDialog(name, ips, self)
        if dlg.exec() == QDialog.Accepted:
            new_name, new_ips = dlg.get_data()
            if not new_ips: return
            for old_ip in list(devs.keys()):
                if devs[old_ip] == name and old_ip not in new_ips: del devs[old_ip]
            for i in new_ips: devs[i] = new_name or new_ips[0]
            self.history["devices"] = devs
            self._update_history("last_ip", new_ips[0])
            self._populate_device_list()
            self.ui.update_device_combo_style(new_name or new_ips[0])

    def _remove_tv(self):
        ip = self.get_selected_ip()
        if ip in self.history.get("devices", {}):
            del self.history["devices"][ip]
            self._save_timer.start()
            self._populate_device_list()

    def _toggle_audio_paused(self):
        self.session.allow_audio_when_paused = not self.session.allow_audio_when_paused
        self._update_history("allow_audio_when_paused", self.session.allow_audio_when_paused)
        self.session.update_audio_pause_state()

    def collapse_to_mini(self):
        self.is_mini_mode = True
        self.topmost_timer.setInterval(200)
        self.ui.stack.setCurrentWidget(self.ui.mini_container)
        self.ui.update_card_style(0.35, True)
        self.ui.update_mini_bar_style("#8f9bb3", 0.35, True)
        self.setFixedSize(48, 16)
        self.enforce_always_on_top()
        if sys.platform.startswith("linux"): ensure_kwin_rules()

    def expand_window(self):
        self.is_mini_mode = False
        self.pulse_timer.stop()
        self.topmost_timer.setInterval(1000)
        self.ui.stack.setCurrentWidget(self.ui.card)
        op = self.ui.opacity_slider.value() / 100.0
        self.ui.update_card_style(op, False)
        self.setFixedSize(self._expanded_size)
        self.enforce_always_on_top()

    def _on_pulse_step(self):
        import math, time
        el = time.time() - self._pulse_start
        if el >= 3.0 or not self.is_mini_mode:
            self.pulse_timer.stop()
            self.ui.update_card_style(0.35, True)
            self.ui.update_mini_bar_style("#8f9bb3", 0.35, True)
            return
        osc = (math.sin(el * math.pi * 3.5) + 1.0) / 2.0
        self.ui.update_card_style(0.30 + (0.60 * osc), True)
        self.enforce_always_on_top()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.enforce_always_on_top()
            self._drag_start_pos, self._window_start_pos, self._is_dragging = e.globalPosition().toPoint(), self.pos(), False

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.LeftButton:
            diff = e.globalPosition().toPoint() - self._drag_start_pos
            if diff.manhattanLength() > 2:
                self._is_dragging = True
                wh = self.windowHandle()
                if wh and hasattr(wh, "startSystemMove") and wh.startSystemMove(): return
                self.move(self._window_start_pos + diff)
                if self.is_mini_mode: self.enforce_always_on_top()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            if self.is_mini_mode and not self._is_dragging:
                self.toggle_pause() if e.modifiers() & Qt.ControlModifier else self.expand_window()
            self.enforce_always_on_top()

    def enterEvent(self, e):
        if not self.ui.device_combo._is_popup_open:
            if self.is_mini_mode and not self.pulse_timer.isActive(): self.ui.update_card_style(1.0, True)
            self.enforce_always_on_top()
        super().enterEvent(e)

    def leaveEvent(self, e):
        if not self.ui.device_combo._is_popup_open:
            if self.is_mini_mode and not self.pulse_timer.isActive(): self.ui.update_card_style(0.35, True)
        super().leaveEvent(e)

    def keyPressEvent(self, e: QKeyEvent):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and not self.is_mini_mode:
            self.toggle_connect()
        super().keyPressEvent(e)

    def showEvent(self, e):
        super().showEvent(e)
        self.enforce_always_on_top()
        if sys.platform == "win32": exclude_from_capture(self)

    def closeEvent(self, e):
        self.session.cleanup()
        save_history(self.history)
        super().closeEvent(e)