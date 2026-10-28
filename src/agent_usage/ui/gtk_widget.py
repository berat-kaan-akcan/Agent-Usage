"""GTK3 + AyatanaAppIndicator3 Desktop Widget implementation for Linux (KDE Plasma, GNOME, X11, Wayland)."""

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


def is_gtk_available() -> bool:
    try:
        import gi

        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk  # noqa: F401

        return True
    except Exception:
        return False


def run_gtk_widget(start_minimized: bool = False, self_test: bool = False) -> int:
    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import Gdk, GLib, Gtk

    try:
        gi.require_version("AyatanaAppIndicator3", "0.1")
        from gi.repository import AyatanaAppIndicator3 as AppIndicator
    except Exception:
        try:
            gi.require_version("AppIndicator3", "0.1")
            from gi.repository import AppIndicator3 as AppIndicator
        except Exception:
            AppIndicator = None

    class AgentUsageGtkWindow(Gtk.Window):
        def __init__(self) -> None:
            super().__init__(title="Agent Usage Widget")
            self.env: PlatformEnvironment = detect_platform_environment()
            self.settings_store = SettingsStore()
            self.settings: WidgetSettings = self.settings_store.load()
            if start_minimized:
                self.settings.start_minimized = True

            self.orchestrator = UsageOrchestrator()
            self.snapshots: Dict[str, ProviderSnapshot] = self.orchestrator.get_cached_snapshots()
            self._timer_id: Optional[int] = None
            self._refreshing: bool = False

            self.set_default_size(self.settings.window_width, self.settings.window_height)
            self.set_resizable(True)
            self.set_keep_above(self.settings.always_on_top)
            if self.env.session_type == "x11":
                self.move(self.settings.window_x, self.settings.window_y)

            self.connect("delete-event", self._on_delete_event)
            self.connect("configure-event", self._on_configure_event)

            self._css_provider = Gtk.CssProvider()
            screen = Gdk.Screen.get_default()
            if screen is not None:
                Gtk.StyleContext.add_provider_for_screen(
                    screen,
                    self._css_provider,
                    Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
                )

            self._build_ui()
            self._apply_theme_and_opacity()
            self._setup_tray(AppIndicator)
            self._render_snapshots()
            self._schedule_timer()

            # Trigger initial background refresh
            self.trigger_refresh()

        def _build_ui(self) -> None:
            self.root_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            self.root_box.set_margin_top(10)
            self.root_box.set_margin_bottom(10)
            self.root_box.set_margin_start(12)
            self.root_box.set_margin_end(12)
            self.add(self.root_box)

            # Top Header Bar (Draggable area + Quick Actions)
            header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            header.get_style_context().add_class("header-bar")

            title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            self.title_label = Gtk.Label(label="AI Agent Usage")
            self.title_label.set_xalign(0.0)
            self.title_label.get_style_context().add_class("widget-title")
            self.subtitle_label = Gtk.Label(label="Initializing...")
            self.subtitle_label.set_xalign(0.0)
            self.subtitle_label.get_style_context().add_class("muted-text")
            title_box.pack_start(self.title_label, False, False, 0)
            title_box.pack_start(self.subtitle_label, False, False, 0)
            header.pack_start(title_box, True, True, 0)

            # Period selector
            self.period_combo = Gtk.ComboBoxText()
            for pid, plabel in [
                ("five_hour", "5h Window"),
                ("today", "Today"),
                ("seven_day", "7 Days"),
                ("thirty_day", "30 Days"),
                ("all_time", "All Time"),
            ]:
                self.period_combo.append(pid, plabel)
            self.period_combo.set_active_id(self.settings.selected_time_period)
            self.period_combo.connect("changed", self._on_period_changed)
            header.pack_start(self.period_combo, False, False, 0)

            # Compact / Expanded toggle
            self.view_btn = Gtk.Button(
                label="Compact" if self.settings.view_mode == "expanded" else "Expand"
            )
            self.view_btn.set_tooltip_text("Toggle Compact / Expanded view")
            self.view_btn.connect("clicked", self._on_toggle_view_mode)
            header.pack_start(self.view_btn, False, False, 0)

            # Refresh button
            self.refresh_btn = Gtk.Button(label="Refresh")
            self.refresh_btn.set_tooltip_text("Manual Refresh Now")
            self.refresh_btn.connect("clicked", lambda *_: self.trigger_refresh())
            header.pack_start(self.refresh_btn, False, False, 0)

            # Settings toggle button
            self.settings_btn = Gtk.ToggleButton(label="⚙")
            self.settings_btn.set_tooltip_text("Widget Settings")
            self.settings_btn.connect("toggled", self._on_toggle_settings)
            header.pack_start(self.settings_btn, False, False, 0)

            self.root_box.pack_start(header, False, False, 0)

            # Collapsible Settings Panel
            self.settings_revealer = Gtk.Revealer()
            self.settings_revealer.set_transition_type(
                Gtk.RevealerTransitionType.SLIDE_DOWN
            )
            self._build_settings_panel()
            self.root_box.pack_start(self.settings_revealer, False, False, 0)

            # Scrollable Provider Cards Area
            self.scrolled = Gtk.ScrolledWindow()
            self.scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            self.cards_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            self.scrolled.add(self.cards_box)
            self.root_box.pack_start(self.scrolled, True, True, 0)

            # Platform note footer
            self.footer_label = Gtk.Label(
                label=f"{self.env.desktop_env} ({self.env.session_type.upper()})"
            )
            self.footer_label.set_xalign(0.0)
            self.footer_label.set_line_wrap(True)
            self.footer_label.get_style_context().add_class("muted-text")
            self.root_box.pack_start(self.footer_label, False, False, 0)

        def _build_settings_panel(self) -> None:
            panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            panel.get_style_context().add_class("provider-card")

            # Theme row
            row1 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            row1.pack_start(Gtk.Label(label="Theme:", xalign=0), False, False, 0)
            theme_combo = Gtk.ComboBoxText()
            theme_combo.append("dark", "Dark Theme")
            theme_combo.append("light", "Light Theme")
            theme_combo.set_active_id(self.settings.theme)
            theme_combo.connect("changed", self._on_theme_changed)
            row1.pack_end(theme_combo, False, False, 0)
            panel.pack_start(row1, False, False, 0)

            # Opacity slider
            row2 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            row2.pack_start(Gtk.Label(label="Opacity:", xalign=0), False, False, 0)
            adj = Gtk.Adjustment(
                value=self.settings.opacity * 100.0,
                lower=40.0,
                upper=100.0,
                step_increment=5.0,
                page_increment=10.0,
            )
            opacity_scale = Gtk.Scale(
                orientation=Gtk.Orientation.HORIZONTAL, adjustment=adj
            )
            opacity_scale.set_digits(0)
            opacity_scale.set_hexpand(True)
            opacity_scale.connect("value-changed", self._on_opacity_changed)
            row2.pack_start(opacity_scale, True, True, 0)
            panel.pack_start(row2, False, False, 0)

            # Refresh interval spin
            row3 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            row3.pack_start(
                Gtk.Label(label="Refresh Interval (sec):", xalign=0), False, False, 0
            )
            spin_adj = Gtk.Adjustment(
                value=float(self.settings.refresh_interval_seconds),
                lower=15.0,
                upper=3600.0,
                step_increment=15.0,
                page_increment=60.0,
            )
            refresh_spin = Gtk.SpinButton(adjustment=spin_adj, climb_rate=1.0, digits=0)
            refresh_spin.connect("value-changed", self._on_interval_changed)
            row3.pack_end(refresh_spin, False, False, 0)
            panel.pack_start(row3, False, False, 0)

            # Checkboxes
            aot_cb = Gtk.CheckButton(label="Always on Top (where supported by compositor)")
            aot_cb.set_active(self.settings.always_on_top)
            aot_cb.set_tooltip_text(self.env.always_on_top_note)
            aot_cb.connect("toggled", self._on_aot_toggled)
            panel.pack_start(aot_cb, False, False, 0)

            tray_cb = Gtk.CheckButton(label="Minimize to System Tray on Close")
            tray_cb.set_active(self.settings.minimize_to_tray)
            tray_cb.connect("toggled", self._on_minimize_tray_toggled)
            panel.pack_start(tray_cb, False, False, 0)

            auto_cb = Gtk.CheckButton(label="Start Automatically on System Login")
            auto_cb.set_active(self.settings.start_with_system)
            auto_cb.connect("toggled", self._on_autostart_toggled)
            panel.pack_start(auto_cb, False, False, 0)

            net_cb = Gtk.CheckButton(label="Poll Live Account Quota APIs")
            net_cb.set_active(self.settings.network_quota_enabled)
            net_cb.connect("toggled", self._on_network_toggled)
            panel.pack_start(net_cb, False, False, 0)

            note_lbl = Gtk.Label(label=self.env.always_on_top_note)
            note_lbl.set_xalign(0.0)
            note_lbl.set_line_wrap(True)
            note_lbl.get_style_context().add_class("muted-text")
            panel.pack_start(note_lbl, False, False, 0)

            self.settings_revealer.add(panel)

        def _setup_tray(self, AppIndicatorModule: object) -> None:
            self.indicator = None
            if AppIndicatorModule is None:
                return
            try:
                self.indicator = AppIndicatorModule.Indicator.new(
                    "agent-usage-widget",
                    "utilities-system-monitor",
                    AppIndicatorModule.IndicatorCategory.APPLICATION_STATUS,
                )
                self.indicator.set_status(AppIndicatorModule.IndicatorStatus.ACTIVE)
                menu = Gtk.Menu()

                show_item = Gtk.MenuItem(label="Show / Hide Widget")
                show_item.connect("activate", self._on_tray_toggle_visible)
                menu.append(show_item)

                refresh_item = Gtk.MenuItem(label="Refresh Usage Now")
                refresh_item.connect("activate", lambda *_: self.trigger_refresh())
                menu.append(refresh_item)

                mode_item = Gtk.MenuItem(label="Toggle Compact / Expanded")
                mode_item.connect("activate", self._on_toggle_view_mode)
                menu.append(mode_item)

                menu.append(Gtk.SeparatorMenuItem())

                quit_item = Gtk.MenuItem(label="Quit Agent Usage")
                quit_item.connect("activate", self._on_quit)
                menu.append(quit_item)

                menu.show_all()
                self.indicator.set_menu(menu)
            except Exception:
                self.indicator = None

        def _apply_theme_and_opacity(self) -> None:
            p = get_palette(self.settings.theme)
            css = f"""
            window {{
                background-color: {p.bg_primary};
                color: {p.text_primary};
            }}
            .widget-title {{
                font-weight: bold;
                font-size: 15px;
                color: {p.text_primary};
            }}
            .provider-card {{
                background-color: {p.bg_card};
                border: 1px solid {p.border};
                border-radius: 8px;
                padding: 10px;
            }}
            .provider-header {{
                font-weight: bold;
                font-size: 14px;
                color: {p.text_primary};
            }}
            .secondary-text {{
                font-size: 12px;
                color: {p.text_secondary};
            }}
            .muted-text {{
                font-size: 11px;
                color: {p.text_muted};
            }}
            .badge-ok {{
                color: {p.status_ok};
                font-weight: bold;
                font-size: 11px;
            }}
            .badge-warn {{
                color: {p.status_warn};
                font-weight: bold;
                font-size: 11px;
            }}
            .badge-err {{
                color: {p.status_err};
                font-weight: bold;
                font-size: 11px;
            }}
            """
            self._css_provider.load_from_data(css.encode("utf-8"))
            self.set_opacity(self.settings.opacity)

        def trigger_refresh(self) -> None:
            if self._refreshing:
                return
            self._refreshing = True
            self.refresh_btn.set_sensitive(False)
            self.subtitle_label.set_text("Refreshing usage & quotas...")

            def _on_done(snaps: Dict[str, ProviderSnapshot]) -> None:
                GLib.idle_add(self._handle_refresh_complete, snaps)

            self.orchestrator.refresh_async(_on_done, settings=self.settings)

        def _handle_refresh_complete(self, snaps: Dict[str, ProviderSnapshot]) -> bool:
            self._refreshing = False
            self.refresh_btn.set_sensitive(True)
            self.snapshots = snaps
            now_str = datetime.now().strftime("%H:%M:%S")
            self.subtitle_label.set_text(f"Last updated: {now_str}")
            self._render_snapshots()
            return False

        def _render_snapshots(self) -> None:
            for child in self.cards_box.get_children():
                self.cards_box.remove(child)

            is_compact = self.settings.view_mode == "compact"
            period = self.settings.selected_time_period

            for pid in ("claude", "codex", "antigravity"):
                if pid not in self.settings.enabled_providers:
                    continue
                snap = self.snapshots.get(pid)
                if not snap:
                    continue
                card = self._build_provider_card(snap, is_compact, period)
                self.cards_box.pack_start(card, False, False, 0)

            self.cards_box.show_all()

        def _build_provider_card(
            self, snap: ProviderSnapshot, is_compact: bool, period: str
        ) -> Gtk.Widget:
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            card.get_style_context().add_class("provider-card")

            # Header row: Provider Name + Auth Badge
            top_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            name_lbl = Gtk.Label(label=snap.display_name)
            name_lbl.set_xalign(0.0)
            name_lbl.get_style_context().add_class("provider-header")
            top_row.pack_start(name_lbl, True, True, 0)

            badge_text = snap.auth_state.value.upper()
            if snap.is_stale:
                badge_text += " • STALE"
            badge_lbl = Gtk.Label(label=badge_text)
            if snap.auth_state == AuthState.AUTHENTICATED and not snap.is_stale:
                badge_lbl.get_style_context().add_class("badge-ok")
            elif snap.auth_state in (AuthState.LOCAL_ONLY, AuthState.RATE_LIMITED) or snap.is_stale:
                badge_lbl.get_style_context().add_class("badge-warn")
            else:
                badge_lbl.get_style_context().add_class("badge-err")
            top_row.pack_end(badge_lbl, False, False, 0)
            card.pack_start(top_row, False, False, 0)

            # Plan / Credits subtitle
            if snap.plan_name or snap.credits_label:
                meta_parts = [p for p in (snap.plan_name, snap.credits_label) if p]
                meta_lbl = Gtk.Label(label=" • ".join(meta_parts))
                meta_lbl.set_xalign(0.0)
                meta_lbl.get_style_context().add_class("secondary-text")
                card.pack_start(meta_lbl, False, False, 0)

            # Local Token Usage summary line
            stats: TokenUsageStats = getattr(snap, f"usage_{period}", snap.usage_seven_day)
            tok_summary = (
                f"Local Tokens ({period}): {format_tokens(stats.total_tokens)} total "
                f"(In: {format_tokens(stats.input_tokens)} | "
                f"Cache: {format_tokens(stats.cache_read_tokens + stats.cache_creation_tokens)} | "
                f"Out: {format_tokens(stats.output_tokens)}) • {stats.session_count} sess"
            )
            tok_lbl = Gtk.Label(label=tok_summary)
            tok_lbl.set_xalign(0.0)
            tok_lbl.set_line_wrap(True)
            tok_lbl.get_style_context().add_class("secondary-text")
            card.pack_start(tok_lbl, False, False, 0)

            # Quota Windows progress bars
            if snap.quota_windows:
                max_windows = 2 if is_compact else len(snap.quota_windows)
                for qw in snap.quota_windows[:max_windows]:
                    qbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
                    qrow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
                    qlbl = Gtk.Label(label=qw.label)
                    qlbl.set_xalign(0.0)
                    qlbl.get_style_context().add_class("muted-text")
                    qrow.pack_start(qlbl, True, True, 0)

                    countdown = qw.time_until_reset_human()
                    rtext = f"{qw.used_percent:.1f}% used ({qw.remaining_percent:.1f}% left)"
                    if countdown:
                        rtext += f" • resets {countdown}"
                    rlbl = Gtk.Label(label=rtext)
                    rlbl.get_style_context().add_class("muted-text")
                    qrow.pack_end(rlbl, False, False, 0)
                    qbox.pack_start(qrow, False, False, 0)

                    pbar = Gtk.ProgressBar()
                    pbar.set_fraction(max(0.0, min(1.0, (qw.used_percent or 0.0) / 100.0)))
                    qbox.pack_start(pbar, False, False, 0)
                    card.pack_start(qbox, False, False, 0)

                if is_compact and len(snap.quota_windows) > max_windows:
                    more_lbl = Gtk.Label(
                        label=f"+{len(snap.quota_windows) - max_windows} more model quota windows in Expanded view"
                    )
                    more_lbl.set_xalign(0.0)
                    more_lbl.get_style_context().add_class("muted-text")
                    card.pack_start(more_lbl, False, False, 0)

            if not is_compact:
                # Model breakdown
                if snap.model_usage_all_time:
                    m_header = Gtk.Label(label="Recorded Local Usage by Model (All-Time):")
                    m_header.set_xalign(0.0)
                    m_header.get_style_context().add_class("muted-text")
                    card.pack_start(m_header, False, False, 0)
                    for mname, mstats in list(snap.model_usage_all_time.items())[:5]:
                        mlbl = Gtk.Label(
                            label=f"  • {mname}: {format_tokens(mstats.total_tokens)} "
                            f"(In {format_tokens(mstats.input_tokens)}, Out {format_tokens(mstats.output_tokens)})"
                        )
                        mlbl.set_xalign(0.0)
                        mlbl.get_style_context().add_class("muted-text")
                        card.pack_start(mlbl, False, False, 0)

                # Status / Stale / Scope indicators
                status_lbl = Gtk.Label(label=snap.status_message)
                status_lbl.set_xalign(0.0)
                status_lbl.set_line_wrap(True)
                status_lbl.get_style_context().add_class("muted-text")
                card.pack_start(status_lbl, False, False, 0)

                if snap.is_stale and snap.stale_reason:
                    stale_lbl = Gtk.Label(label=f"Stale Data: {snap.stale_reason}")
                    stale_lbl.set_xalign(0.0)
                    stale_lbl.set_line_wrap(True)
                    stale_lbl.get_style_context().add_class("badge-warn")
                    card.pack_start(stale_lbl, False, False, 0)

                scope_lbl = Gtk.Label(label=snap.scope_note)
                scope_lbl.set_xalign(0.0)
                scope_lbl.set_line_wrap(True)
                scope_lbl.get_style_context().add_class("muted-text")
                card.pack_start(scope_lbl, False, False, 0)

            return card

        def _schedule_timer(self) -> None:
            if self._timer_id is not None:
                GLib.source_remove(self._timer_id)
            interval_ms = max(15, self.settings.refresh_interval_seconds) * 1000
            self._timer_id = GLib.timeout_add(interval_ms, self._on_timer_tick)

        def _on_timer_tick(self) -> bool:
            self.trigger_refresh()
            return True

        def _on_period_changed(self, combo: Gtk.ComboBoxText) -> None:
            active_id = combo.get_active_id()
            if active_id:
                self.settings.selected_time_period = active_id
                self.settings_store.save(self.settings)
                self._render_snapshots()

        def _on_toggle_view_mode(self, *_: object) -> None:
            if self.settings.view_mode == "expanded":
                self.settings.view_mode = "compact"
                self.view_btn.set_label("Expand")
                self.resize(self.settings.window_width, 320)
            else:
                self.settings.view_mode = "expanded"
                self.view_btn.set_label("Compact")
                self.resize(self.settings.window_width, max(540, self.settings.window_height))
            self.settings_store.save(self.settings)
            self._render_snapshots()

        def _on_toggle_settings(self, btn: Gtk.ToggleButton) -> None:
            self.settings_revealer.set_reveal_child(btn.get_active())

        def _on_theme_changed(self, combo: Gtk.ComboBoxText) -> None:
            tid = combo.get_active_id()
            if tid in ("dark", "light"):
                self.settings.theme = tid
                self.settings_store.save(self.settings)
                self._apply_theme_and_opacity()

        def _on_opacity_changed(self, scale: Gtk.Scale) -> None:
            val = scale.get_value() / 100.0
            self.settings.opacity = val
            self.set_opacity(val)
            self.settings_store.save(self.settings)

        def _on_interval_changed(self, spin: Gtk.SpinButton) -> None:
            self.settings.refresh_interval_seconds = int(spin.get_value())
            self.settings_store.save(self.settings)
            self._schedule_timer()

        def _on_aot_toggled(self, cb: Gtk.CheckButton) -> None:
            self.settings.always_on_top = cb.get_active()
            self.set_keep_above(self.settings.always_on_top)
            self.settings_store.save(self.settings)

        def _on_minimize_tray_toggled(self, cb: Gtk.CheckButton) -> None:
            self.settings.minimize_to_tray = cb.get_active()
            self.settings_store.save(self.settings)

        def _on_autostart_toggled(self, cb: Gtk.CheckButton) -> None:
            self.settings.start_with_system = cb.get_active()
            AutostartManager.set_enabled(self.settings.start_with_system)
            self.settings_store.save(self.settings)

        def _on_network_toggled(self, cb: Gtk.CheckButton) -> None:
            self.settings.network_quota_enabled = cb.get_active()
            self.settings_store.save(self.settings)
            self.trigger_refresh()

        def _on_configure_event(self, _widget: Gtk.Widget, event: Gdk.EventConfigure) -> bool:
            if self.settings.view_mode == "expanded":
                self.settings.window_width = event.width
                self.settings.window_height = event.height
            self.settings.window_x = event.x
            self.settings.window_y = event.y
            return False

        def _on_delete_event(self, *_: object) -> bool:
            self.settings_store.save(self.settings)
            if self.settings.minimize_to_tray and self.indicator is not None:
                self.hide()
                return True
            self._on_quit()
            return False

        def _on_tray_toggle_visible(self, *_: object) -> None:
            if self.get_visible():
                self.hide()
            else:
                self.show_all()
                self.present()

        def _on_quit(self, *_: object) -> None:
            self.settings_store.save(self.settings)
            self.orchestrator.shutdown()
            Gtk.main_quit()

    win = AgentUsageGtkWindow()
    if not win.settings.start_minimized:
        win.show_all()
    if self_test:
        GLib.timeout_add(400, lambda: (win._on_quit(), False)[1])
    Gtk.main()
    return 0
