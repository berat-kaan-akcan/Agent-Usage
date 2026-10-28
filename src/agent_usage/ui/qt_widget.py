"""Qt6 (PySide6 / PyQt6) Cross-Platform Desktop Widget for Windows 10/11 and Linux."""

from __future__ import annotations

import sys
from datetime import datetime
from typing import Dict, Optional

from ..cli import format_tokens
from ..core.models import AuthState, ProviderSnapshot, TokenUsageStats
from ..core.orchestrator import UsageOrchestrator
from ..core.platform_info import AutostartManager, PlatformEnvironment, detect_platform_environment
from ..core.settings import SettingsStore, WidgetSettings
from .theme import get_palette


def is_qt_available() -> bool:
    for mod in ("PySide6", "PyQt6"):
        try:
            __import__(f"{mod}.QtWidgets")
            return True
        except ImportError:
            continue
    return False


def run_qt_widget(start_minimized: bool = False, self_test: bool = False) -> int:
    try:
        from PySide6 import QtCore, QtGui, QtWidgets

        Signal = QtCore.Signal
    except ImportError:
        from PyQt6 import QtCore, QtGui, QtWidgets  # type: ignore

        Signal = QtCore.pyqtSignal  # type: ignore

    class RefreshBridge(QtCore.QObject):
        completed = Signal(object)

    class AgentUsageQtWindow(QtWidgets.QWidget):
        def __init__(self) -> None:
            super().__init__()
            self.env: PlatformEnvironment = detect_platform_environment()
            self.settings_store = SettingsStore()
            self.settings: WidgetSettings = self.settings_store.load()
            if start_minimized:
                self.settings.start_minimized = True

            self.orchestrator = UsageOrchestrator()
            self.snapshots: Dict[str, ProviderSnapshot] = self.orchestrator.get_cached_snapshots()
            self._bridge = RefreshBridge()
            self._bridge.completed.connect(self._on_refresh_done)
            self._refreshing = False
            self._drag_pos: Optional[QtCore.QPoint] = None

            self.setWindowTitle("Agent Usage Widget")
            self._apply_window_flags()
            self.resize(self.settings.window_width, self.settings.window_height)
            if self.env.session_type in ("windows", "x11", "macos"):
                self.move(self.settings.window_x, self.settings.window_y)

            self._build_ui()
            self._setup_tray()
            self._apply_theme_and_opacity()
            self._render_snapshots()

            self._timer = QtCore.QTimer(self)
            self._timer.timeout.connect(self.trigger_refresh)
            self._timer.start(max(15, self.settings.refresh_interval_seconds) * 1000)

            self.trigger_refresh()

        def _apply_window_flags(self) -> None:
            flags = QtCore.Qt.WindowType.Window
            if self.settings.always_on_top:
                flags |= QtCore.Qt.WindowType.WindowStaysOnTopHint
            self.setWindowFlags(flags)

        def _build_ui(self) -> None:
            self.main_layout = QtWidgets.QVBoxLayout(self)
            self.main_layout.setContentsMargins(12, 10, 12, 10)
            self.main_layout.setSpacing(8)

            # Header Row
            header = QtWidgets.QHBoxLayout()
            title_col = QtWidgets.QVBoxLayout()
            self.title_lbl = QtWidgets.QLabel("AI Agent Usage")
            self.title_lbl.setObjectName("widgetTitle")
            self.subtitle_lbl = QtWidgets.QLabel("Initializing...")
            self.subtitle_lbl.setObjectName("mutedText")
            title_col.addWidget(self.title_lbl)
            title_col.addWidget(self.subtitle_lbl)
            header.addLayout(title_col, 1)

            self.period_combo = QtWidgets.QComboBox()
            for pid, plabel in [
                ("five_hour", "5h Window"),
                ("today", "Today"),
                ("seven_day", "7 Days"),
                ("thirty_day", "30 Days"),
                ("all_time", "All Time"),
            ]:
                self.period_combo.addItem(plabel, pid)
            idx = self.period_combo.findData(self.settings.selected_time_period)
            if idx >= 0:
                self.period_combo.setCurrentIndex(idx)
            self.period_combo.currentIndexChanged.connect(self._on_period_changed)
            header.addWidget(self.period_combo)

            self.view_btn = QtWidgets.QPushButton(
                "Compact" if self.settings.view_mode == "expanded" else "Expand"
            )
            self.view_btn.clicked.connect(self._on_toggle_view_mode)
            header.addWidget(self.view_btn)

            self.refresh_btn = QtWidgets.QPushButton("Refresh")
            self.refresh_btn.clicked.connect(self.trigger_refresh)
            header.addWidget(self.refresh_btn)

            self.settings_btn = QtWidgets.QPushButton("⚙")
            self.settings_btn.setCheckable(True)
            self.settings_btn.toggled.connect(self._on_toggle_settings)
            header.addWidget(self.settings_btn)

            self.main_layout.addLayout(header)

            # Settings Frame
            self.settings_frame = QtWidgets.QFrame()
            self.settings_frame.setObjectName("providerCard")
            self.settings_frame.setVisible(False)
            self._build_settings_panel()
            self.main_layout.addWidget(self.settings_frame)

            # Scroll Area for Provider Cards
            self.scroll = QtWidgets.QScrollArea()
            self.scroll.setWidgetResizable(True)
            self.scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
            self.cards_container = QtWidgets.QWidget()
            self.cards_layout = QtWidgets.QVBoxLayout(self.cards_container)
            self.cards_layout.setContentsMargins(0, 0, 0, 0)
            self.cards_layout.setSpacing(10)
            self.scroll.setWidget(self.cards_container)
            self.main_layout.addWidget(self.scroll, 1)

            # Footer
            self.footer_lbl = QtWidgets.QLabel(
                f"{self.env.desktop_env} ({self.env.session_type.upper()})"
            )
            self.footer_lbl.setObjectName("mutedText")
            self.footer_lbl.setWordWrap(True)
            self.main_layout.addWidget(self.footer_lbl)

        def _build_settings_panel(self) -> None:
            slayout = QtWidgets.QVBoxLayout(self.settings_frame)
            slayout.setSpacing(6)

            # Theme
            row1 = QtWidgets.QHBoxLayout()
            row1.addWidget(QtWidgets.QLabel("Theme:"))
            self.theme_combo = QtWidgets.QComboBox()
            self.theme_combo.addItem("Dark Theme", "dark")
            self.theme_combo.addItem("Light Theme", "light")
            t_idx = self.theme_combo.findData(self.settings.theme)
            if t_idx >= 0:
                self.theme_combo.setCurrentIndex(t_idx)
            self.theme_combo.currentIndexChanged.connect(self._on_theme_changed)
            row1.addWidget(self.theme_combo)
            slayout.addLayout(row1)

            # Opacity
            row2 = QtWidgets.QHBoxLayout()
            row2.addWidget(QtWidgets.QLabel("Opacity:"))
            self.opacity_slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
            self.opacity_slider.setRange(40, 100)
            self.opacity_slider.setValue(int(self.settings.opacity * 100))
            self.opacity_slider.valueChanged.connect(self._on_opacity_changed)
            row2.addWidget(self.opacity_slider)
            slayout.addLayout(row2)

            # Refresh interval
            row3 = QtWidgets.QHBoxLayout()
            row3.addWidget(QtWidgets.QLabel("Refresh Interval (sec):"))
            self.interval_spin = QtWidgets.QSpinBox()
            self.interval_spin.setRange(15, 3600)
            self.interval_spin.setSingleStep(15)
            self.interval_spin.setValue(self.settings.refresh_interval_seconds)
            self.interval_spin.valueChanged.connect(self._on_interval_changed)
            row3.addWidget(self.interval_spin)
            slayout.addLayout(row3)

            # Checkboxes
            self.aot_cb = QtWidgets.QCheckBox("Always on Top")
            self.aot_cb.setChecked(self.settings.always_on_top)
            self.aot_cb.setToolTip(self.env.always_on_top_note)
            self.aot_cb.toggled.connect(self._on_aot_toggled)
            slayout.addWidget(self.aot_cb)

            self.tray_cb = QtWidgets.QCheckBox("Minimize to System Tray on Close")
            self.tray_cb.setChecked(self.settings.minimize_to_tray)
            self.tray_cb.toggled.connect(self._on_tray_toggled)
            slayout.addWidget(self.tray_cb)

            self.auto_cb = QtWidgets.QCheckBox("Start Automatically on System Login")
            self.auto_cb.setChecked(self.settings.start_with_system)
            self.auto_cb.toggled.connect(self._on_autostart_toggled)
            slayout.addWidget(self.auto_cb)

            self.net_cb = QtWidgets.QCheckBox("Poll Live Account Quota APIs")
            self.net_cb.setChecked(self.settings.network_quota_enabled)
            self.net_cb.toggled.connect(self._on_net_toggled)
            slayout.addWidget(self.net_cb)

            note = QtWidgets.QLabel(self.env.always_on_top_note)
            note.setObjectName("mutedText")
            note.setWordWrap(True)
            slayout.addWidget(note)

        def _setup_tray(self) -> None:
            self.tray_icon: Optional[QtWidgets.QSystemTrayIcon] = None
            if not QtWidgets.QSystemTrayIcon.isSystemTrayAvailable():
                return
            self.tray_icon = QtWidgets.QSystemTrayIcon(self)
            icon = self.style().standardIcon(
                QtWidgets.QStyle.StandardPixmap.SP_ComputerIcon
            )
            self.tray_icon.setIcon(icon)
            self.tray_icon.setToolTip("Agent Usage Widget")

            menu = QtWidgets.QMenu(self)
            show_act = menu.addAction("Show / Hide Widget")
            show_act.triggered.connect(self._toggle_visibility)
            ref_act = menu.addAction("Refresh Usage Now")
            ref_act.triggered.connect(self.trigger_refresh)
            mode_act = menu.addAction("Toggle Compact / Expanded")
            mode_act.triggered.connect(self._on_toggle_view_mode)
            menu.addSeparator()
            quit_act = menu.addAction("Quit Agent Usage")
            quit_act.triggered.connect(self._quit_app)

            self.tray_icon.setContextMenu(menu)
            self.tray_icon.activated.connect(self._on_tray_activated)
            self.tray_icon.show()

        def _apply_theme_and_opacity(self) -> None:
            p = get_palette(self.settings.theme)
            self.setStyleSheet(
                f"""
                QWidget {{
                    background-color: {p.bg_primary};
                    color: {p.text_primary};
                    font-family: sans-serif;
                }}
                #widgetTitle {{
                    font-size: 15px;
                    font-weight: bold;
                    color: {p.text_primary};
                }}
                #providerCard {{
                    background-color: {p.bg_card};
                    border: 1px solid {p.border};
                    border-radius: 8px;
                    padding: 8px;
                }}
                #providerHeader {{
                    font-size: 14px;
                    font-weight: bold;
                    color: {p.text_primary};
                    background: transparent;
                }}
                #secondaryText {{
                    font-size: 12px;
                    color: {p.text_secondary};
                    background: transparent;
                }}
                #mutedText {{
                    font-size: 11px;
                    color: {p.text_muted};
                    background: transparent;
                }}
                #badgeOk {{
                    color: {p.status_ok};
                    font-weight: bold;
                    font-size: 11px;
                    background: transparent;
                }}
                #badgeWarn {{
                    color: {p.status_warn};
                    font-weight: bold;
                    font-size: 11px;
                    background: transparent;
                }}
                #badgeErr {{
                    color: {p.status_err};
                    font-weight: bold;
                    font-size: 11px;
                    background: transparent;
                }}
                QPushButton, QComboBox, QSpinBox {{
                    background-color: {p.bg_card};
                    border: 1px solid {p.border};
                    border-radius: 4px;
                    padding: 4px 8px;
                    color: {p.text_primary};
                }}
                QProgressBar {{
                    border: 1px solid {p.border};
                    border-radius: 4px;
                    background-color: {p.progress_bg};
                    text-align: center;
                    height: 12px;
                    font-size: 10px;
                }}
                QProgressBar::chunk {{
                    background-color: {p.accent_antigravity};
                    border-radius: 3px;
                }}
                """
            )
            self.setWindowOpacity(self.settings.opacity)

        def trigger_refresh(self) -> None:
            if self._refreshing:
                return
            self._refreshing = True
            self.refresh_btn.setEnabled(False)
            self.subtitle_lbl.setText("Refreshing usage & quotas...")
            self.orchestrator.refresh_async(
                lambda snaps: self._bridge.completed.emit(snaps),
                settings=self.settings,
            )

        def _on_refresh_done(self, snaps: Dict[str, ProviderSnapshot]) -> None:
            self._refreshing = False
            self.refresh_btn.setEnabled(True)
            self.snapshots = snaps
            now_str = datetime.now().strftime("%H:%M:%S")
            self.subtitle_lbl.setText(f"Last updated: {now_str}")
            self._render_snapshots()

        def _render_snapshots(self) -> None:
            while self.cards_layout.count():
                item = self.cards_layout.takeAt(0)
                w = item.widget()
                if w is not None:
                    w.deleteLater()

            is_compact = self.settings.view_mode == "compact"
            period = self.settings.selected_time_period

            for pid in ("claude", "codex", "antigravity"):
                if pid not in self.settings.enabled_providers:
                    continue
                snap = self.snapshots.get(pid)
                if not snap:
                    continue
                self.cards_layout.addWidget(
                    self._build_provider_card(snap, is_compact, period)
                )
            self.cards_layout.addStretch(1)

        def _build_provider_card(
            self, snap: ProviderSnapshot, is_compact: bool, period: str
        ) -> QtWidgets.QWidget:
            card = QtWidgets.QFrame()
            card.setObjectName("providerCard")
            clayout = QtWidgets.QVBoxLayout(card)
            clayout.setSpacing(5)

            top_row = QtWidgets.QHBoxLayout()
            name_lbl = QtWidgets.QLabel(snap.display_name)
            name_lbl.setObjectName("providerHeader")
            top_row.addWidget(name_lbl, 1)

            btext = snap.auth_state.value.upper()
            if snap.is_stale:
                btext += " • STALE"
            badge_lbl = QtWidgets.QLabel(btext)
            if snap.auth_state == AuthState.AUTHENTICATED and not snap.is_stale:
                badge_lbl.setObjectName("badgeOk")
            elif (
                snap.auth_state in (AuthState.LOCAL_ONLY, AuthState.RATE_LIMITED)
                or snap.is_stale
            ):
                badge_lbl.setObjectName("badgeWarn")
            else:
                badge_lbl.setObjectName("badgeErr")
            top_row.addWidget(badge_lbl)
            clayout.addLayout(top_row)

            if snap.plan_name or snap.credits_label:
                meta = " • ".join([x for x in (snap.plan_name, snap.credits_label) if x])
                mlbl = QtWidgets.QLabel(meta)
                mlbl.setObjectName("secondaryText")
                clayout.addWidget(mlbl)

            stats: TokenUsageStats = getattr(snap, f"usage_{period}", snap.usage_seven_day)
            tok_lbl = QtWidgets.QLabel(
                f"Local Tokens ({period}): {format_tokens(stats.total_tokens)} total "
                f"(In: {format_tokens(stats.input_tokens)} | "
                f"Cache: {format_tokens(stats.cache_read_tokens + stats.cache_creation_tokens)} | "
                f"Out: {format_tokens(stats.output_tokens)}) • {stats.session_count} sess"
            )
            tok_lbl.setObjectName("secondaryText")
            tok_lbl.setWordWrap(True)
            clayout.addWidget(tok_lbl)

            if snap.quota_windows:
                limit = 2 if is_compact else len(snap.quota_windows)
                for qw in snap.quota_windows[:limit]:
                    qrow = QtWidgets.QHBoxLayout()
                    qlbl = QtWidgets.QLabel(qw.label)
                    qlbl.setObjectName("mutedText")
                    qrow.addWidget(qlbl, 1)

                    countdown = qw.time_until_reset_human()
                    rtext = f"{qw.used_percent:.1f}% used ({qw.remaining_percent:.1f}% left)"
                    if countdown:
                        rtext += f" • resets {countdown}"
                    rlbl = QtWidgets.QLabel(rtext)
                    rlbl.setObjectName("mutedText")
                    qrow.addWidget(rlbl)
                    clayout.addLayout(qrow)

                    pbar = QtWidgets.QProgressBar()
                    pbar.setRange(0, 100)
                    pbar.setValue(int(round(qw.used_percent or 0.0)))
                    clayout.addWidget(pbar)

            if not is_compact:
                if snap.model_usage_all_time:
                    mh = QtWidgets.QLabel("Recorded Local Usage by Model (All-Time):")
                    mh.setObjectName("mutedText")
                    clayout.addWidget(mh)
                    for mname, mstats in list(snap.model_usage_all_time.items())[:5]:
                        ml = QtWidgets.QLabel(
                            f"  • {mname}: {format_tokens(mstats.total_tokens)} "
                            f"(In {format_tokens(mstats.input_tokens)}, Out {format_tokens(mstats.output_tokens)})"
                        )
                        ml.setObjectName("mutedText")
                        clayout.addWidget(ml)

                st_lbl = QtWidgets.QLabel(snap.status_message)
                st_lbl.setObjectName("mutedText")
                st_lbl.setWordWrap(True)
                clayout.addWidget(st_lbl)

                sc_lbl = QtWidgets.QLabel(snap.scope_note)
                sc_lbl.setObjectName("mutedText")
                sc_lbl.setWordWrap(True)
                clayout.addWidget(sc_lbl)

            return card

        def _on_period_changed(self, _idx: int) -> None:
            val = self.period_combo.currentData()
            if val:
                self.settings.selected_time_period = str(val)
                self.settings_store.save(self.settings)
                self._render_snapshots()

        def _on_toggle_view_mode(self) -> None:
            if self.settings.view_mode == "expanded":
                self.settings.view_mode = "compact"
                self.view_btn.setText("Expand")
                self.resize(self.width(), 320)
            else:
                self.settings.view_mode = "expanded"
                self.view_btn.setText("Compact")
                self.resize(self.width(), max(540, self.settings.window_height))
            self.settings_store.save(self.settings)
            self._render_snapshots()

        def _on_toggle_settings(self, checked: bool) -> None:
            self.settings_frame.setVisible(checked)

        def _on_theme_changed(self, _idx: int) -> None:
            val = self.theme_combo.currentData()
            if val in ("dark", "light"):
                self.settings.theme = str(val)
                self.settings_store.save(self.settings)
                self._apply_theme_and_opacity()

        def _on_opacity_changed(self, value: int) -> None:
            self.settings.opacity = value / 100.0
            self.setWindowOpacity(self.settings.opacity)
            self.settings_store.save(self.settings)

        def _on_interval_changed(self, value: int) -> None:
            self.settings.refresh_interval_seconds = value
            self.settings_store.save(self.settings)
            self._timer.start(max(15, value) * 1000)

        def _on_aot_toggled(self, checked: bool) -> None:
            self.settings.always_on_top = checked
            self.settings_store.save(self.settings)
            was_vis = self.isVisible()
            self._apply_window_flags()
            if was_vis:
                self.show()

        def _on_tray_toggled(self, checked: bool) -> None:
            self.settings.minimize_to_tray = checked
            self.settings_store.save(self.settings)

        def _on_autostart_toggled(self, checked: bool) -> None:
            self.settings.start_with_system = checked
            AutostartManager.set_enabled(checked)
            self.settings_store.save(self.settings)

        def _on_net_toggled(self, checked: bool) -> None:
            self.settings.network_quota_enabled = checked
            self.settings_store.save(self.settings)
            self.trigger_refresh()

        def _on_tray_activated(self, reason: object) -> None:
            if reason == QtWidgets.QSystemTrayIcon.ActivationReason.Trigger:
                self._toggle_visibility()

        def _toggle_visibility(self) -> None:
            if self.isVisible():
                self.hide()
            else:
                self.showNormal()
                self.raise_()
                self.activateWindow()

        def closeEvent(self, event: QtGui.QCloseEvent) -> None:
            self._save_geometry()
            if self.settings.minimize_to_tray and self.tray_icon is not None:
                event.ignore()
                self.hide()
                return
            self.orchestrator.shutdown()
            event.accept()

        def _save_geometry(self) -> None:
            if self.settings.view_mode == "expanded":
                self.settings.window_width = self.width()
                self.settings.window_height = self.height()
            pos = self.pos()
            self.settings.window_x = pos.x()
            self.settings.window_y = pos.y()
            self.settings_store.save(self.settings)

        def _quit_app(self) -> None:
            self._save_geometry()
            self.orchestrator.shutdown()
            QtWidgets.QApplication.quit()

    app = QtWidgets.QApplication(sys.argv)
    win = AgentUsageQtWindow()
    if not win.settings.start_minimized:
        win.show()
    if self_test:
        QtCore.QTimer.singleShot(400, win._quit_app)
    return app.exec()
