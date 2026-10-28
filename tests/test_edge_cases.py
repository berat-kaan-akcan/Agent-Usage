"""Stress tests for malformed files, corrupted databases, changed schemas, and network timeouts."""

from __future__ import annotations

import base64
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from agent_usage.core.models import AuthState
from agent_usage.core.platform_info import discover_provider_paths
from agent_usage.providers.antigravity import AntigravityCollector, decode_protobuf_fields
from agent_usage.providers.claude import ClaudeCollector
from agent_usage.providers.codex import CodexCollector


class TestEdgeCasesAndSchemaResilience(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 10, 28, 12, 0, 0, tzinfo=timezone.utc)

    def test_claude_corrupted_credentials_and_negative_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = discover_provider_paths(home_dir=Path(tmpdir))
            paths.claude_dir = Path(tmpdir) / ".claude"
            paths.claude_projects_dir = paths.claude_dir / "projects"
            paths.claude_credentials_file = paths.claude_dir / ".credentials.json"
            paths.claude_projects_dir.mkdir(parents=True, exist_ok=True)

            # Write corrupted credentials JSON
            paths.claude_credentials_file.write_text("{not valid json", encoding="utf-8")

            # Write session with negative token counts (must be rejected) and unexpected schema types
            sfile = paths.claude_projects_dir / "bad.jsonl"
            sfile.write_text(
                "\n".join(
                    [
                        json.dumps(["a list instead of dict"]),
                        json.dumps({"type": "assistant", "message": "not a dict"}),
                        json.dumps(
                            {
                                "type": "assistant",
                                "message": {
                                    "id": "bad1",
                                    "usage": {"input_tokens": -500, "output_tokens": 100},
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "type": "assistant",
                                "message": {
                                    "id": "good1",
                                    "usage": {"input_tokens": 200, "output_tokens": 80},
                                },
                            }
                        ),
                    ]
                ),
                encoding="utf-8",
            )

            collector = ClaudeCollector(paths=paths)
            snap = collector.collect(allow_network=False, now=self.now)
            self.assertEqual(snap.auth_state, AuthState.LOCAL_ONLY)
            self.assertEqual(snap.usage_all_time.total_tokens, 280)

    def test_codex_network_timeout_falls_back_to_embedded_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = discover_provider_paths(home_dir=Path(tmpdir))
            paths.codex_dir = Path(tmpdir) / ".codex"
            paths.codex_sessions_dir = paths.codex_dir / "sessions"
            paths.codex_auth_file = paths.codex_dir / "auth.json"
            paths.codex_sessions_dir.mkdir(parents=True, exist_ok=True)

            paths.codex_auth_file.write_text(
                json.dumps(
                    {
                        "auth_mode": "chatgpt",
                        "tokens": {"access_token": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sig"},
                    }
                ),
                encoding="utf-8",
            )
            rfile = paths.codex_sessions_dir / "rollout-1.jsonl"
            rfile.write_text(
                json.dumps(
                    {
                        "timestamp": "2026-10-28T11:30:00Z",
                        "type": "event_msg",
                        "payload": {
                            "type": "token_count",
                            "info": {
                                "last_token_usage": {
                                    "input_tokens": 600,
                                    "cached_input_tokens": 200,
                                    "output_tokens": 150,
                                }
                            },
                            "rate_limits": {
                                "plan_type": "plus",
                                "primary": {"used_percent": 31.0, "window_minutes": 300},
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )

            def timeout_http_get(url, headers, timeout):
                raise TimeoutError("Connection timed out after 5.0s")

            collector = CodexCollector(paths=paths, http_get_fn=timeout_http_get)
            snap = collector.collect(allow_network=True, now=self.now)
            self.assertEqual(snap.auth_state, AuthState.LOCAL_ONLY)
            self.assertEqual(snap.usage_all_time.total_tokens, 750)
            self.assertEqual(len(snap.quota_windows), 1)
            self.assertEqual(snap.quota_windows[0].used_percent, 31.0)

    def test_antigravity_truncated_protobuf_and_changed_sqlite_schema(self) -> None:
        # Test truncated / random bytes in protobuf decoder never crash
        fields = decode_protobuf_fields(b"\x80\x80\x80\x80\x80\x80\x80\x80\x80\x80\x01")
        self.assertIsInstance(fields, list)
        fields2 = decode_protobuf_fields(b"\x0a\xff\x01short")
        self.assertEqual(fields2, [])

        with tempfile.TemporaryDirectory() as tmpdir:
            paths = discover_provider_paths(home_dir=Path(tmpdir))
            paths.antigravity_gemini_dir = Path(tmpdir) / ".gemini" / "antigravity"
            paths.antigravity_brain_dir = paths.antigravity_gemini_dir / "brain"
            paths.antigravity_log_files = []

            # SQLite DB without ItemTable (changed schema)
            db_path = Path(tmpdir) / "state.vscdb"
            conn = sqlite3.connect(str(db_path))
            conn.execute("CREATE TABLE OtherTable (id INT)")
            conn.commit()
            conn.close()

            paths.antigravity_state_dbs = [db_path]
            collector = AntigravityCollector(paths=paths)
            snap = collector.collect(allow_network=False, now=self.now)
            self.assertEqual(snap.auth_state, AuthState.UNAUTHENTICATED)
            self.assertTrue(len(snap.warnings) > 0)


if __name__ == "__main__":
    unittest.main()
