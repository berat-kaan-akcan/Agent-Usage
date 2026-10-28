"""Tests for core models, settings persistence, snapshot cache, autostart, and credential redaction."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_usage.core.models import (
    AuthState,
    ProviderSnapshot,
    QuotaSourceType,
    QuotaWindow,
    TokenUsageStats,
)
from agent_usage.core.platform_info import (
    AutostartManager,
    detect_platform_environment,
    discover_provider_paths,
)
from agent_usage.core.settings import SettingsStore, SnapshotCache, WidgetSettings
from agent_usage.providers.base import parse_iso_timestamp, sanitize_message


class TestCoreModelsAndSettings(unittest.TestCase):
    def test_token_usage_stats_addition_and_serialization(self) -> None:
        s1 = TokenUsageStats(
            input_tokens=100,
            cache_creation_tokens=200,
            cache_read_tokens=300,
            output_tokens=50,
            reasoning_output_tokens=10,
            message_count=1,
            session_count=1,
        )
        s2 = TokenUsageStats(
            input_tokens=50,
            cache_creation_tokens=0,
            cache_read_tokens=100,
            output_tokens=25,
            reasoning_output_tokens=5,
            message_count=2,
            session_count=1,
        )
        s1.add(s2)
        self.assertEqual(s1.input_tokens, 150)
        self.assertEqual(s1.cache_creation_tokens, 200)
        self.assertEqual(s1.cache_read_tokens, 400)
        self.assertEqual(s1.output_tokens, 75)
        self.assertEqual(s1.reasoning_output_tokens, 15)
        self.assertEqual(s1.total_tokens, 150 + 200 + 400 + 75)
        self.assertEqual(s1.active_tokens, 150 + 75)

        restored = TokenUsageStats.from_dict(s1.to_dict())
        self.assertEqual(restored.total_tokens, s1.total_tokens)
        self.assertEqual(restored.message_count, 3)

    def test_quota_window_calculations_and_countdown(self) -> None:
        now = datetime(2026, 10, 28, 0, 0, 0, tzinfo=timezone.utc)
        reset = (now + timedelta(hours=3, minutes=30)).isoformat()
        qw = QuotaWindow(id="five_hour", label="5h Window", used_percent=35.5, resets_at=reset)
        self.assertAlmostEqual(qw.remaining_percent or 0.0, 64.5)
        self.assertFalse(qw.is_exhausted)
        self.assertEqual(qw.time_until_reset_human(now=now), "3h 30m")

        qw_exhausted = QuotaWindow(id="opus", label="Opus", remaining_percent=0.0)
        self.assertEqual(qw_exhausted.used_percent, 100.0)
        self.assertTrue(qw_exhausted.is_exhausted)

    def test_settings_validation_and_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings_file = Path(tmpdir) / "settings.json"
            store = SettingsStore(settings_file)

            # Default when missing
            loaded = store.load()
            self.assertEqual(loaded.view_mode, "expanded")
            self.assertEqual(loaded.theme, "dark")

            # Clamp out-of-range values
            bad = WidgetSettings(
                view_mode="invalid_mode",
                theme="neon",
                opacity=5.0,
                refresh_interval_seconds=2,
                window_width=50,
                window_height=50,
                selected_time_period="invalid_period",
            )
            store.save(bad)
            reloaded = store.load()
            self.assertEqual(reloaded.view_mode, "expanded")
            self.assertEqual(reloaded.theme, "dark")
            self.assertEqual(reloaded.opacity, 1.0)
            self.assertEqual(reloaded.refresh_interval_seconds, 15)
            self.assertEqual(reloaded.window_width, 280)
            self.assertEqual(reloaded.window_height, 180)
            self.assertEqual(reloaded.selected_time_period, "seven_day")

    def test_snapshot_cache_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / "cache.json"
            cache = SnapshotCache(cache_file)

            snap = ProviderSnapshot(
                provider_id="claude",
                display_name="Claude Code",
                auth_state=AuthState.AUTHENTICATED,
                quota_source=QuotaSourceType.OAUTH_API,
                plan_name="Claude Pro",
                usage_seven_day=TokenUsageStats(input_tokens=1200, output_tokens=400, message_count=3),
                quota_windows=[
                    QuotaWindow(id="five_hour", label="5-Hour Window", used_percent=42.0)
                ],
            )
            cache.save_all({"claude": snap})
            loaded = cache.load_all()
            self.assertIn("claude", loaded)
            self.assertEqual(loaded["claude"].plan_name, "Claude Pro")
            self.assertEqual(loaded["claude"].usage_seven_day.total_tokens, 1600)
            self.assertEqual(len(loaded["claude"].quota_windows), 1)
            self.assertEqual(loaded["claude"].quota_windows[0].used_percent, 42.0)

    def test_autostart_manager_linux_desktop_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_home = Path(tmpdir)
            self.assertFalse(AutostartManager.is_enabled(config_home=cfg_home))
            AutostartManager.set_enabled(
                True, exec_command="/usr/bin/agent-usage-qt --minimized", config_home=cfg_home
            )
            self.assertTrue(AutostartManager.is_enabled(config_home=cfg_home))
            desktop_file = AutostartManager.get_linux_autostart_file(config_home=cfg_home)
            content = desktop_file.read_text(encoding="utf-8")
            self.assertIn("Exec=/usr/bin/agent-usage-qt --minimized", content)
            self.assertIn("X-GNOME-Autostart-enabled=true", content)

            AutostartManager.set_enabled(False, config_home=cfg_home)
            self.assertFalse(AutostartManager.is_enabled(config_home=cfg_home))

    def test_sanitize_message_redacts_secrets(self) -> None:
        raw = (
            "Failed with Bearer sk-ant-oat01-ABCDEF1234567890 and "
            "jwt eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c "
            "and google ya29.a0AfH6SMBx1234567890abcdef"
        )
        cleaned = sanitize_message(raw)
        self.assertNotIn("sk-ant-oat01", cleaned)
        self.assertNotIn("eyJhbGci", cleaned)
        self.assertNotIn("ya29.a0Af", cleaned)
        self.assertIn("[REDACTED]", cleaned)

    def test_parse_iso_timestamp_variants(self) -> None:
        dt1 = parse_iso_timestamp("2026-10-28T04:36:34Z")
        self.assertIsNotNone(dt1)
        self.assertEqual(dt1.year, 2026)

        # Epoch seconds
        dt2 = parse_iso_timestamp(1793162194)
        self.assertIsNotNone(dt2)

        # Epoch milliseconds
        dt3 = parse_iso_timestamp(1793162194000)
        self.assertEqual(dt2, dt3)

        self.assertIsNone(parse_iso_timestamp("not-a-timestamp"))
        self.assertIsNone(parse_iso_timestamp(None))

    def test_platform_and_path_discovery(self) -> None:
        env = detect_platform_environment()
        self.assertIn(env.os_name, ("linux", "windows", "darwin"))
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = discover_provider_paths(home_dir=Path(tmpdir))
            self.assertTrue(str(paths.claude_dir).endswith(".claude") or "claude" in str(paths.claude_dir))


if __name__ == "__main__":
    unittest.main()
