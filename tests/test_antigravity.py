"""Unit and fixture tests for AntigravityCollector and Protobuf state decoder."""

from __future__ import annotations

import base64
import json
import sqlite3
import struct
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_usage.core.models import AuthState, QuotaSourceType
from agent_usage.core.platform_info import discover_provider_paths
from agent_usage.providers.antigravity import AntigravityCollector


def _encode_varint(val: int) -> bytes:
    out = bytearray()
    while True:
        b = val & 0x7F
        val >>= 7
        if val:
            out.append(b | 0x80)
        else:
            out.append(b)
            break
    return bytes(out)


def _encode_tag(field_num: int, wire_type: int) -> bytes:
    return _encode_varint((field_num << 3) | wire_type)


def _encode_bytes_field(field_num: int, payload: bytes) -> bytes:
    return _encode_tag(field_num, 2) + _encode_varint(len(payload)) + payload


def _encode_float_field(field_num: int, val: float) -> bytes:
    return _encode_tag(field_num, 5) + struct.pack("<f", val)


def _encode_varint_field(field_num: int, val: int) -> bytes:
    return _encode_tag(field_num, 0) + _encode_varint(val)


def build_mock_user_status_outer_b64() -> str:
    """Construct a realistic Antigravity UserStatus protobuf wrapped in outer base64."""
    # Model 1: Gemini 3.8 Flash (High) -> 80% remaining, resets at 1793200000
    ts_msg = _encode_varint_field(1, 1793200000)
    qinfo_1 = _encode_float_field(1, 0.80) + _encode_bytes_field(2, ts_msg)
    model_1 = _encode_bytes_field(1, b"Gemini 3.8 Flash (High)") + _encode_bytes_field(
        15, qinfo_1
    )

    # Model 2: Claude Sonnet 5.5 (High) -> 40% remaining, resets at 1793210000
    ts_msg_2 = _encode_varint_field(1, 1793210000)
    qinfo_2 = _encode_float_field(1, 0.40) + _encode_bytes_field(2, ts_msg_2)
    model_2 = _encode_bytes_field(1, b"Claude Sonnet 5.5 (High)") + _encode_bytes_field(
        15, qinfo_2
    )

    cascade_field_33 = _encode_bytes_field(1, model_1) + _encode_bytes_field(1, model_2)
    plan_field_36 = _encode_bytes_field(2, b"Google AI Pro")
    credits_field_38 = _encode_varint_field(13, 250)

    inner_proto = (
        _encode_bytes_field(33, cascade_field_33)
        + _encode_bytes_field(36, plan_field_36)
        + _encode_bytes_field(38, credits_field_38)
    )
    inner_b64 = base64.b64encode(inner_proto)

    # Outer wrapper with sentinel key + inner_b64
    outer_proto = _encode_bytes_field(
        1,
        _encode_bytes_field(1, b"userStatusSentinelKey")
        + _encode_bytes_field(2, _encode_bytes_field(1, inner_b64)),
    )
    return base64.b64encode(outer_proto).decode("ascii")


class TestAntigravityCollector(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 10, 28, 12, 0, 0, tzinfo=timezone.utc)

    def test_not_installed_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = discover_provider_paths(home_dir=Path(tmpdir))
            paths.antigravity_gemini_dir = Path(tmpdir) / ".gemini" / "antigravity"
            paths.antigravity_brain_dir = paths.antigravity_gemini_dir / "brain"
            paths.antigravity_state_dbs = []
            paths.antigravity_log_files = []

            collector = AntigravityCollector(paths=paths)
            snap = collector.collect(allow_network=False, now=self.now)
            self.assertEqual(snap.auth_state, AuthState.NOT_INSTALLED)

    def test_transcript_parsing_and_never_inferring_quota_from_local_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = discover_provider_paths(home_dir=Path(tmpdir))
            paths.antigravity_gemini_dir = Path(tmpdir) / ".gemini" / "antigravity"
            paths.antigravity_brain_dir = paths.antigravity_gemini_dir / "brain"
            paths.antigravity_state_dbs = []
            paths.antigravity_log_files = []

            conv_dir = paths.antigravity_brain_dir / "conv-111" / ".system_generated" / "logs"
            conv_dir.mkdir(parents=True, exist_ok=True)
            tfile = conv_dir / "transcript.jsonl"

            ts_1h = (self.now - timedelta(hours=1)).isoformat()
            lines = [
                json.dumps(
                    {
                        "step_index": 0,
                        "source": "USER",
                        "content": "<USER_SETTINGS_CHANGE>The user changed setting `Model Selection` from None to Gemini 4 Argon (Medium). No need to comment.</USER_SETTINGS_CHANGE>",
                    }
                ),
                json.dumps(
                    {
                        "step_index": 1,
                        "source": "MODEL",
                        "type": "PLANNER_RESPONSE",
                        "created_at": ts_1h,
                        "input_tokens": 4000,
                        "cache_read_tokens": 10000,
                        "output_tokens": 600,
                    }
                ),
                # Duplicate step_index 1 with final token counts
                json.dumps(
                    {
                        "step_index": 1,
                        "source": "MODEL",
                        "type": "PLANNER_RESPONSE",
                        "created_at": ts_1h,
                        "input_tokens": 4000,
                        "cache_read_tokens": 10000,
                        "output_tokens": 800,
                    }
                ),
                "corrupted json line",
            ]
            tfile.write_text("\n".join(lines), encoding="utf-8")

            collector = AntigravityCollector(paths=paths)
            snap = collector.collect(allow_network=False, now=self.now)

            self.assertEqual(snap.auth_state, AuthState.LOCAL_ONLY)
            self.assertEqual(snap.usage_five_hour.total_tokens, 14800)
            self.assertEqual(snap.usage_five_hour.input_tokens, 4000)
            self.assertEqual(snap.usage_five_hour.cache_read_tokens, 10000)
            self.assertEqual(snap.usage_five_hour.output_tokens, 800)
            self.assertIn("Gemini 4 Argon (Medium)", snap.model_usage_all_time)
            # Crucial rule: Never infer remaining quota from local token history!
            self.assertEqual(len(snap.quota_windows), 0)

    def test_state_vscdb_protobuf_quota_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = discover_provider_paths(home_dir=Path(tmpdir))
            paths.antigravity_gemini_dir = Path(tmpdir) / ".gemini" / "antigravity"
            paths.antigravity_brain_dir = paths.antigravity_gemini_dir / "brain"

            db_path = Path(tmpdir) / "state.vscdb"
            conn = sqlite3.connect(str(db_path))
            conn.execute("CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value BLOB)")
            conn.execute(
                "INSERT INTO ItemTable (key, value) VALUES (?, ?)",
                ("antigravityUnifiedStateSync.userStatus", build_mock_user_status_outer_b64()),
            )
            conn.commit()
            conn.close()

            paths.antigravity_state_dbs = [db_path]
            paths.antigravity_log_files = []

            collector = AntigravityCollector(paths=paths)
            snap = collector.collect(allow_network=False, now=self.now)

            self.assertEqual(snap.auth_state, AuthState.AUTHENTICATED)
            self.assertEqual(snap.quota_source, QuotaSourceType.LOCAL_STATE_DB)
            self.assertEqual(snap.plan_name, "Google AI Pro")
            self.assertEqual(snap.available_credits, 250.0)
            self.assertEqual(len(snap.quota_windows), 2)

            q1 = snap.quota_windows[0]
            self.assertEqual(q1.label, "Gemini 3.8 Flash (High)")
            self.assertAlmostEqual(q1.remaining_percent or 0.0, 80.0, places=1)
            self.assertAlmostEqual(q1.used_percent or 0.0, 20.0, places=1)

            q2 = snap.quota_windows[1]
            self.assertEqual(q2.label, "Claude Sonnet 5.5 (High)")
            self.assertAlmostEqual(q2.remaining_percent or 0.0, 40.0, places=1)
            self.assertAlmostEqual(q2.used_percent or 0.0, 60.0, places=1)

    def test_local_language_server_connect_rpc_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = discover_provider_paths(home_dir=Path(tmpdir))
            paths.antigravity_gemini_dir = Path(tmpdir) / ".gemini" / "antigravity"
            paths.antigravity_brain_dir = paths.antigravity_gemini_dir / "brain"
            paths.antigravity_state_dbs = []

            log_file = Path(tmpdir) / "language_server.log"
            log_file.write_text(
                "I1028 02:31:10.547565 1 server.go:695] Language server listening on random port at 42563 for HTTP\n",
                encoding="utf-8",
            )
            paths.antigravity_log_files = [log_file]

            def mock_post(url, headers, body, timeout):
                self.assertIn("127.0.0.1:42563", url)
                return 200, {
                    "userStatus": {
                        "planStatus": {
                            "planInfo": {"planName": "Google AI Ultra"},
                            "availablePromptCredits": 999,
                        },
                        "cascadeModelConfigData": {
                            "clientModelConfigs": [
                                {
                                    "label": "Gemini 3.8 Pro",
                                    "quotaInfo": {
                                        "remainingFraction": 0.90,
                                        "resetTime": "2026-10-28T18:00:00Z",
                                    },
                                }
                            ]
                        },
                    }
                }

            collector = AntigravityCollector(paths=paths, http_post_fn=mock_post)
            snap = collector.collect(allow_network=True, now=self.now)

            self.assertEqual(snap.auth_state, AuthState.AUTHENTICATED)
            self.assertEqual(snap.quota_source, QuotaSourceType.LOCAL_LANGUAGE_SERVER)
            self.assertEqual(snap.plan_name, "Google AI Ultra")
            self.assertEqual(snap.available_credits, 999.0)
            self.assertEqual(len(snap.quota_windows), 1)
            self.assertEqual(snap.quota_windows[0].used_percent, 10.0)


if __name__ == "__main__":
    unittest.main()
