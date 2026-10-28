"""Unit and fixture tests for CodexCollector."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_usage.core.models import AuthState, QuotaSourceType
from agent_usage.core.platform_info import discover_provider_paths
from agent_usage.providers.codex import CodexCollector


class TestCodexCollector(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 10, 28, 12, 0, 0, tzinfo=timezone.utc)

    def test_not_installed_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = discover_provider_paths(home_dir=Path(tmpdir))
            paths.codex_dir = Path(tmpdir) / ".codex"
            paths.codex_sessions_dir = paths.codex_dir / "sessions"
            paths.codex_auth_file = paths.codex_dir / "auth.json"

            collector = CodexCollector(paths=paths)
            snap = collector.collect(allow_network=False, now=self.now)
            self.assertEqual(snap.auth_state, AuthState.NOT_INSTALLED)

    def test_rollout_cumulative_deltas_duplicates_and_embedded_rate_limits(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = discover_provider_paths(home_dir=Path(tmpdir))
            paths.codex_dir = Path(tmpdir) / ".codex"
            paths.codex_sessions_dir = paths.codex_dir / "sessions"
            paths.codex_auth_file = paths.codex_dir / "auth.json"

            day_dir = paths.codex_sessions_dir / "2026" / "10" / "28"
            day_dir.mkdir(parents=True, exist_ok=True)
            rollout_file = day_dir / "rollout-2026-10-28-abc.jsonl"

            ts_1 = (self.now - timedelta(minutes=30)).isoformat()
            ts_2 = (self.now - timedelta(minutes=15)).isoformat()
            reset_5h = int((self.now + timedelta(hours=4)).timestamp())
            reset_7d = int((self.now + timedelta(days=5)).timestamp())

            events = [
                json.dumps({"timestamp": ts_1, "type": "session_meta", "payload": {"model": "gpt-5-codex"}}),
                # Turn 1 cumulative: 2000 input (1000 cached), 500 output (100 reasoning)
                json.dumps(
                    {
                        "timestamp": ts_1,
                        "type": "event_msg",
                        "payload": {
                            "type": "token_count",
                            "info": {
                                "total_token_usage": {
                                    "input_tokens": 2000,
                                    "cached_input_tokens": 1000,
                                    "output_tokens": 500,
                                    "reasoning_output_tokens": 100,
                                    "total_tokens": 2500,
                                }
                            },
                            "rate_limits": {
                                "plan_type": "plus",
                                "primary": {
                                    "used_percent": 12.0,
                                    "window_minutes": 300,
                                    "resets_at": reset_5h,
                                },
                                "secondary": {
                                    "used_percent": 4.5,
                                    "window_minutes": 10080,
                                    "resets_at": reset_7d,
                                },
                            },
                        },
                    }
                ),
                # Duplicate cumulative event (must be ignored!)
                json.dumps(
                    {
                        "timestamp": ts_1,
                        "type": "event_msg",
                        "payload": {
                            "type": "token_count",
                            "info": {
                                "total_token_usage": {
                                    "input_tokens": 2000,
                                    "cached_input_tokens": 1000,
                                    "output_tokens": 500,
                                    "reasoning_output_tokens": 100,
                                    "total_tokens": 2500,
                                }
                            },
                        },
                    }
                ),
                # Turn 2 cumulative: 3500 input (2000 cached), 800 output (200 reasoning)
                # Delta = +500 uncached input, +1000 cached, +300 output
                json.dumps(
                    {
                        "timestamp": ts_2,
                        "type": "event_msg",
                        "payload": {
                            "type": "token_count",
                            "info": {
                                "total_token_usage": {
                                    "input_tokens": 3500,
                                    "cached_input_tokens": 2000,
                                    "output_tokens": 800,
                                    "reasoning_output_tokens": 200,
                                    "total_tokens": 4300,
                                }
                            },
                            "rate_limits": {
                                "plan_type": "plus",
                                "primary": {
                                    "used_percent": 18.5,
                                    "window_minutes": 300,
                                    "resets_at": reset_5h,
                                },
                                "secondary": {
                                    "used_percent": 5.0,
                                    "window_minutes": 10080,
                                    "resets_at": reset_7d,
                                },
                            },
                        },
                    }
                ),
                "broken json line",
            ]
            rollout_file.write_text("\n".join(events), encoding="utf-8")

            collector = CodexCollector(paths=paths)
            snap = collector.collect(allow_network=False, now=self.now)

            self.assertEqual(snap.auth_state, AuthState.LOCAL_ONLY)
            self.assertEqual(snap.quota_source, QuotaSourceType.LOCAL_EVENT_SNAPSHOT)
            self.assertEqual(snap.plan_name, "ChatGPT Plus")
            self.assertEqual(snap.usage_five_hour.total_tokens, 4300)
            self.assertEqual(snap.usage_five_hour.input_tokens, 1500)
            self.assertEqual(snap.usage_five_hour.cache_read_tokens, 2000)
            self.assertEqual(snap.usage_five_hour.output_tokens, 800)
            self.assertEqual(snap.usage_five_hour.reasoning_output_tokens, 200)
            self.assertEqual(snap.usage_five_hour.message_count, 2)
            self.assertIn("gpt-5-codex", snap.model_usage_all_time)

            self.assertEqual(len(snap.quota_windows), 2)
            self.assertEqual(snap.quota_windows[0].used_percent, 18.5)
            self.assertEqual(snap.quota_windows[1].used_percent, 5.0)

    def test_distinguishes_api_key_billing_from_subscription_quota(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = discover_provider_paths(home_dir=Path(tmpdir))
            paths.codex_dir = Path(tmpdir) / ".codex"
            paths.codex_sessions_dir = paths.codex_dir / "sessions"
            paths.codex_auth_file = paths.codex_dir / "auth.json"
            paths.codex_dir.mkdir(parents=True, exist_ok=True)

            # Case 1: API Key Billing Mode
            paths.codex_auth_file.write_text(
                json.dumps(
                    {
                        "auth_mode": "api_key",
                        "OPENAI_API_KEY": "sk-proj-1234567890abcdefghijklmnop",
                        "tokens": {},
                    }
                ),
                encoding="utf-8",
            )
            collector = CodexCollector(paths=paths)
            snap_api = collector.collect(allow_network=True, now=self.now)
            self.assertEqual(snap_api.auth_state, AuthState.AUTHENTICATED)
            self.assertEqual(snap_api.billing_mode, "api_billing")
            self.assertEqual(snap_api.quota_source, QuotaSourceType.API_BILLING)
            self.assertEqual(len(snap_api.quota_windows), 0)

            # Case 2: ChatGPT Subscription Mode with live wham/usage endpoint
            paths.codex_auth_file.write_text(
                json.dumps(
                    {
                        "auth_mode": "chatgpt",
                        "OPENAI_API_KEY": None,
                        "tokens": {
                            "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.sig",
                            "account_id": "acct_999",
                        },
                    }
                ),
                encoding="utf-8",
            )

            def mock_wham(url, headers, timeout):
                self.assertEqual(headers.get("ChatGPT-Account-Id"), "acct_999")
                return 200, {
                    "plan_type": "pro",
                    "rate_limit": {
                        "primary_window": {
                            "used_percent": 7.0,
                            "limit_window_seconds": 18000,
                            "reset_at": 1793170000,
                        },
                        "secondary_window": {
                            "used_percent": 22.0,
                            "limit_window_seconds": 604800,
                            "reset_at": 1793500000,
                        },
                    },
                    "credits": {
                        "has_credits": True,
                        "balance": "$45.00",
                    },
                }

            collector_sub = CodexCollector(paths=paths, http_get_fn=mock_wham)
            snap_sub = collector_sub.collect(allow_network=True, now=self.now)
            self.assertEqual(snap_sub.auth_state, AuthState.AUTHENTICATED)
            self.assertEqual(snap_sub.billing_mode, "subscription")
            self.assertEqual(snap_sub.quota_source, QuotaSourceType.OAUTH_API)
            self.assertEqual(snap_sub.plan_name, "ChatGPT Pro")
            self.assertEqual(len(snap_sub.quota_windows), 2)
            self.assertEqual(snap_sub.quota_windows[0].used_percent, 7.0)
            self.assertIn("$45.00", snap_sub.credits_label or "")


if __name__ == "__main__":
    unittest.main()
