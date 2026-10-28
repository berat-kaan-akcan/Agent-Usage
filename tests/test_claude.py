"""Unit and fixture tests for ClaudeCollector."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Tuple

from agent_usage.core.models import AuthState, QuotaSourceType
from agent_usage.core.platform_info import discover_provider_paths
from agent_usage.providers.claude import ClaudeCollector


class TestClaudeCollector(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 10, 28, 12, 0, 0, tzinfo=timezone.utc)

    def test_not_installed_when_directory_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = discover_provider_paths(home_dir=Path(tmpdir))
            # Force claude_dir inside tmpdir
            paths.claude_dir = Path(tmpdir) / ".claude"
            paths.claude_projects_dir = paths.claude_dir / "projects"
            paths.claude_credentials_file = paths.claude_dir / ".credentials.json"

            collector = ClaudeCollector(paths=paths)
            snap = collector.collect(allow_network=False, now=self.now)
            self.assertEqual(snap.auth_state, AuthState.NOT_INSTALLED)
            self.assertEqual(snap.usage_all_time.total_tokens, 0)
            self.assertEqual(len(snap.quota_windows), 0)

    def test_local_session_parsing_deduplication_and_malformed_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = discover_provider_paths(home_dir=Path(tmpdir))
            paths.claude_dir = Path(tmpdir) / ".claude"
            paths.claude_projects_dir = paths.claude_dir / "projects"
            paths.claude_credentials_file = paths.claude_dir / ".credentials.json"

            proj_dir = paths.claude_projects_dir / "-home-user-app"
            proj_dir.mkdir(parents=True, exist_ok=True)
            session_file = proj_dir / "sess-1.jsonl"

            ts_2h_ago = (self.now - timedelta(hours=2)).isoformat()
            ts_3d_ago = (self.now - timedelta(days=3)).isoformat()
            ts_20d_ago = (self.now - timedelta(days=20)).isoformat()

            lines = [
                # Streaming chunk 1 for msg_01 (smaller output_tokens)
                json.dumps(
                    {
                        "type": "assistant",
                        "timestamp": ts_2h_ago,
                        "message": {
                            "id": "msg_01",
                            "role": "assistant",
                            "model": "claude-sonnet-4-5-20250929",
                            "usage": {
                                "input_tokens": 500,
                                "cache_creation_input_tokens": 1000,
                                "cache_read_input_tokens": 2000,
                                "output_tokens": 50,
                            },
                        },
                    }
                ),
                # Streaming chunk 2 for msg_01 (final higher output_tokens - should replace chunk 1!)
                json.dumps(
                    {
                        "type": "assistant",
                        "timestamp": ts_2h_ago,
                        "message": {
                            "id": "msg_01",
                            "role": "assistant",
                            "model": "claude-sonnet-4-5-20250929",
                            "usage": {
                                "input_tokens": 500,
                                "cache_creation_input_tokens": 1000,
                                "cache_read_input_tokens": 2000,
                                "output_tokens": 250,
                            },
                        },
                    }
                ),
                # Malformed line that must be skipped gracefully
                "{malformed json line",
                # User message (should be ignored)
                json.dumps({"type": "user", "message": {"role": "user"}}),
                # Message 3 days ago (Opus)
                json.dumps(
                    {
                        "type": "assistant",
                        "timestamp": ts_3d_ago,
                        "message": {
                            "id": "msg_02",
                            "role": "assistant",
                            "model": "claude-opus-4-1-20250805",
                            "usage": {
                                "input_tokens": 300,
                                "cache_creation_input_tokens": 0,
                                "cache_read_input_tokens": 500,
                                "output_tokens": 200,
                            },
                        },
                    }
                ),
                # Message 20 days ago
                json.dumps(
                    {
                        "type": "assistant",
                        "timestamp": ts_20d_ago,
                        "message": {
                            "id": "msg_03",
                            "role": "assistant",
                            "model": "claude-sonnet-4-5-20250929",
                            "usage": {
                                "input_tokens": 100,
                                "cache_creation_input_tokens": 0,
                                "cache_read_input_tokens": 0,
                                "output_tokens": 100,
                            },
                        },
                    }
                ),
            ]
            session_file.write_text("\n".join(lines), encoding="utf-8")

            collector = ClaudeCollector(paths=paths)
            snap = collector.collect(allow_network=False, now=self.now)

            self.assertEqual(snap.auth_state, AuthState.LOCAL_ONLY)
            # 5h window: only msg_01 (500 + 1000 + 2000 + 250 = 3750)
            self.assertEqual(snap.usage_five_hour.total_tokens, 3750)
            self.assertEqual(snap.usage_five_hour.output_tokens, 250)
            self.assertEqual(snap.usage_five_hour.message_count, 1)

            # 7d window: msg_01 (3750) + msg_02 (1000) = 4750
            self.assertEqual(snap.usage_seven_day.total_tokens, 4750)
            self.assertEqual(snap.usage_seven_day.message_count, 2)

            # 30d / All-Time: msg_01 + msg_02 + msg_03 = 4950
            self.assertEqual(snap.usage_thirty_day.total_tokens, 4950)
            self.assertEqual(snap.usage_all_time.total_tokens, 4950)
            self.assertEqual(snap.usage_all_time.session_count, 1)

            # Model breakdown
            self.assertIn("claude-sonnet-4-5-20250929", snap.model_usage_all_time)
            self.assertIn("claude-opus-4-1-20250805", snap.model_usage_all_time)
            self.assertEqual(
                snap.model_usage_all_time["claude-sonnet-4-5-20250929"].total_tokens,
                3950,
            )
            self.assertEqual(
                snap.model_usage_all_time["claude-opus-4-1-20250805"].total_tokens,
                1000,
            )
            self.assertTrue(any("malformed" in w for w in snap.warnings))

    def test_oauth_quota_success_and_shared_limit_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = discover_provider_paths(home_dir=Path(tmpdir))
            paths.claude_dir = Path(tmpdir) / ".claude"
            paths.claude_projects_dir = paths.claude_dir / "projects"
            paths.claude_credentials_file = paths.claude_dir / ".credentials.json"
            paths.claude_dir.mkdir(parents=True, exist_ok=True)

            future_ms = int((self.now + timedelta(hours=8)).timestamp() * 1000)
            paths.claude_credentials_file.write_text(
                json.dumps(
                    {
                        "claudeAiOauth": {
                            "accessToken": "sk-ant-oat01-validtoken123456",
                            "expiresAt": future_ms,
                            "subscriptionType": "max",
                            "rateLimitTier": "default_claude_max_5x",
                        }
                    }
                ),
                encoding="utf-8",
            )

            def mock_http_get(
                url: str, headers: Dict[str, str], timeout: float
            ) -> Tuple[int, Dict[str, Any]]:
                self.assertEqual(headers.get("anthropic-beta"), "oauth-2025-04-20")
                self.assertTrue(headers.get("User-Agent", "").startswith("claude-code/"))
                return 200, {
                    "five_hour": {
                        "utilization": 28.5,
                        "resets_at": "2026-10-28T15:00:00+00:00",
                    },
                    "seven_day": {
                        "utilization": 14.0,
                        "resets_at": "2026-11-03T00:00:00+00:00",
                    },
                    "seven_day_sonnet": {
                        "utilization": 9.0,
                        "resets_at": "2026-11-03T00:00:00+00:00",
                    },
                    "seven_day_opus": {
                        "utilization": 45.0,
                        "resets_at": "2026-11-03T00:00:00+00:00",
                    },
                    "extra_usage": {
                        "is_enabled": True,
                        "used_credits": 12.50,
                        "monthly_limit": 50.00,
                    },
                }

            collector = ClaudeCollector(paths=paths, http_get_fn=mock_http_get)
            snap = collector.collect(allow_network=True, now=self.now)

            self.assertEqual(snap.auth_state, AuthState.AUTHENTICATED)
            self.assertEqual(snap.quota_source, QuotaSourceType.OAUTH_API)
            self.assertEqual(snap.plan_name, "Claude Max")
            self.assertEqual(len(snap.quota_windows), 4)
            self.assertEqual(snap.quota_windows[0].used_percent, 28.5)
            self.assertEqual(snap.quota_windows[0].remaining_percent, 71.5)
            self.assertIn("Extra Usage: $12.5 / $50.0", snap.credits_label or "")
            self.assertIn("shared across Claude Code, Claude Web", snap.scope_note)

    def test_expired_token_and_rate_limit_fallback_to_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = discover_provider_paths(home_dir=Path(tmpdir))
            paths.claude_dir = Path(tmpdir) / ".claude"
            paths.claude_projects_dir = paths.claude_dir / "projects"
            paths.claude_credentials_file = paths.claude_dir / ".credentials.json"
            paths.claude_dir.mkdir(parents=True, exist_ok=True)

            # First get a valid snapshot to act as cache
            future_ms = int((self.now + timedelta(hours=8)).timestamp() * 1000)
            paths.claude_credentials_file.write_text(
                json.dumps(
                    {
                        "claudeAiOauth": {
                            "accessToken": "sk-ant-oat01-validtoken123456",
                            "expiresAt": future_ms,
                            "subscriptionType": "pro",
                        }
                    }
                ),
                encoding="utf-8",
            )
            collector_ok = ClaudeCollector(
                paths=paths,
                http_get_fn=lambda u, h, t: (
                    200,
                    {"five_hour": {"utilization": 50.0, "resets_at": "2026-10-28T16:00:00Z"}},
                ),
            )
            cached_snap = collector_ok.collect(allow_network=True, now=self.now)

            # Now test HTTP 429 rate limit fallback
            collector_429 = ClaudeCollector(
                paths=paths,
                http_get_fn=lambda u, h, t: (429, {}),
            )
            snap_429 = collector_429.collect(
                allow_network=True, now=self.now, cached_snapshot=cached_snap
            )
            self.assertEqual(snap_429.auth_state, AuthState.RATE_LIMITED)
            self.assertTrue(snap_429.is_stale)
            self.assertEqual(len(snap_429.quota_windows), 1)
            self.assertEqual(snap_429.quota_windows[0].used_percent, 50.0)

            # Now test expired timestamp in credentials file
            past_ms = int((self.now - timedelta(hours=2)).timestamp() * 1000)
            paths.claude_credentials_file.write_text(
                json.dumps(
                    {
                        "claudeAiOauth": {
                            "accessToken": "sk-ant-oat01-expiredtoken12345",
                            "expiresAt": past_ms,
                            "subscriptionType": "pro",
                        }
                    }
                ),
                encoding="utf-8",
            )
            snap_exp = collector_429.collect(
                allow_network=True, now=self.now, cached_snapshot=cached_snap
            )
            self.assertEqual(snap_exp.auth_state, AuthState.EXPIRED)
            self.assertTrue(snap_exp.is_stale)
            self.assertEqual(len(snap_exp.quota_windows), 1)


if __name__ == "__main__":
    unittest.main()
