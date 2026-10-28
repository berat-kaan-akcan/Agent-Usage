"""Google Antigravity local conversation collector and verified model quota decoder."""

from __future__ import annotations

import base64
import json
import re
import sqlite3
import struct
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..core.models import (
    AuthState,
    ProviderSnapshot,
    QuotaSourceType,
    QuotaWindow,
    TokenUsageStats,
)
from ..core.platform_info import ProviderPaths, discover_provider_paths
from .base import BaseProviderCollector, parse_iso_timestamp, sanitize_message


def decode_protobuf_fields(data: bytes) -> List[Tuple[int, str, Any]]:
    """Lightweight, safe wire-format Protobuf decoder for Antigravity UserStatus blobs."""
    idx = 0
    length = len(data)
    fields: List[Tuple[int, str, Any]] = []
    while idx < length:
        tag = 0
        shift = 0
        while idx < length:
            b = data[idx]
            idx += 1
            tag |= (b & 0x7F) << shift
            if not (b & 0x80):
                break
            shift += 7
            if shift > 63:
                return fields
        field_num = tag >> 3
        wire_type = tag & 0x07
        if field_num <= 0:
            break

        if wire_type == 0:  # varint
            val = 0
            shift = 0
            while idx < length:
                b = data[idx]
                idx += 1
                val |= (b & 0x7F) << shift
                if not (b & 0x80):
                    break
                shift += 7
            fields.append((field_num, "varint", val))
        elif wire_type == 1:  # 64-bit fixed
            if idx + 8 > length:
                break
            fields.append((field_num, "fixed64", data[idx : idx + 8]))
            idx += 8
        elif wire_type == 2:  # length-delimited bytes/string/submessage
            sub_len = 0
            shift = 0
            while idx < length:
                b = data[idx]
                idx += 1
                sub_len |= (b & 0x7F) << shift
                if not (b & 0x80):
                    break
                shift += 7
            if sub_len < 0 or idx + sub_len > length:
                break
            fields.append((field_num, "bytes", data[idx : idx + sub_len]))
            idx += sub_len
        elif wire_type == 5:  # 32-bit float / fixed32
            if idx + 4 > length:
                break
            val_f = struct.unpack("<f", data[idx : idx + 4])[0]
            fields.append((field_num, "float32", val_f))
            idx += 4
        else:
            break
    return fields


class AntigravityCollector(BaseProviderCollector):
    """Collects recorded Google Antigravity local transcript usage and verified model quotas.

    Never infers remaining quota from local token history.
    """

    provider_id = "antigravity"
    display_name = "Google Antigravity"

    def __init__(
        self,
        paths: Optional[ProviderPaths] = None,
        http_post_fn: Optional[
            Callable[[str, Dict[str, str], Dict[str, Any], float], Tuple[int, Dict[str, Any]]]
        ] = None,
    ) -> None:
        self.paths = paths or discover_provider_paths()
        self._http_post_fn = http_post_fn or self._default_http_post

    @staticmethod
    def _default_http_post(
        url: str, headers: Dict[str, str], body: Dict[str, Any], timeout: float = 2.5
    ) -> Tuple[int, Dict[str, Any]]:
        raw_body = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=raw_body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status = resp.getcode()
                text = resp.read().decode("utf-8", errors="replace")
                return status, json.loads(text) if text.strip() else {}
        except urllib.error.HTTPError as exc:
            return exc.code, {}

    def collect(
        self,
        allow_network: bool = True,
        now: Optional[datetime] = None,
        cached_snapshot: Optional[ProviderSnapshot] = None,
    ) -> ProviderSnapshot:
        current_time = now or datetime.now(timezone.utc)
        snap = ProviderSnapshot(
            provider_id=self.provider_id,
            display_name=self.display_name,
            last_updated=current_time.isoformat(),
            scope_note=(
                "Local tokens are parsed from ~/.gemini/antigravity/brain/<id>/.system_generated/logs/transcript.jsonl. "
                "Model-specific quotas and reset times are retrieved from Antigravity's verified "
                "UserStatus state (state.vscdb / local language_server) and are never inferred from local tokens."
            ),
            unsupported_notes=[
                "Interactive slash commands (e.g. /compact, /goal) are chat UI shortcuts, not machine-readable quota APIs.",
                "Transcript JSONL steps record exact input_tokens, cache_read_tokens, and output_tokens under source='MODEL'.",
            ],
        )

        brain_dir = self.paths.antigravity_brain_dir
        gemini_dir = self.paths.antigravity_gemini_dir
        existing_dbs = [p for p in self.paths.antigravity_state_dbs if p.is_file()]
        existing_logs = [p for p in self.paths.antigravity_log_files if p.is_file()]

        if gemini_dir.is_dir():
            snap.discovered_paths.append(str(gemini_dir))
        if brain_dir.is_dir():
            snap.discovered_paths.append(str(brain_dir))
        for db in existing_dbs:
            snap.discovered_paths.append(str(db))
        for lg in existing_logs:
            snap.discovered_paths.append(str(lg))

        if not gemini_dir.is_dir() and not existing_dbs and not existing_logs:
            snap.auth_state = AuthState.NOT_INSTALLED
            snap.status_message = (
                f"Google Antigravity directory not found at {gemini_dir}. "
                "Launch Antigravity IDE to record conversations and model quotas."
            )
            return snap

        # 1. Parse local conversation transcripts in ~/.gemini/antigravity/brain/*
        self._collect_local_transcripts(snap, brain_dir, current_time)

        # 2. Try local running language_server Connect-RPC GetUserStatus first (if allow_network)
        loaded_from_ls = False
        if allow_network and existing_logs:
            loaded_from_ls = self._try_local_language_server(snap, existing_logs)

        # 3. Parse verified SQLite state.vscdb (antigravityUnifiedStateSync.userStatus & modelCredits)
        loaded_from_db = False
        if not loaded_from_ls and existing_dbs:
            for db_path in existing_dbs:
                if self._collect_from_state_db(snap, db_path):
                    loaded_from_db = True
                    break

        if loaded_from_ls:
            snap.auth_state = AuthState.AUTHENTICATED
            snap.quota_source = QuotaSourceType.LOCAL_LANGUAGE_SERVER
            snap.billing_mode = "subscription"
            snap.status_message = (
                "Verified model quotas from live Antigravity language_server and local brain transcripts."
            )
        elif loaded_from_db:
            snap.auth_state = AuthState.AUTHENTICATED
            snap.quota_source = QuotaSourceType.LOCAL_STATE_DB
            snap.billing_mode = "subscription"
            snap.status_message = (
                "Verified model quotas from Antigravity IDE state.vscdb and local brain transcripts."
            )
        elif snap.usage_all_time.message_count > 0:
            snap.auth_state = AuthState.LOCAL_ONLY
            snap.status_message = (
                "Loaded local Antigravity brain transcripts. "
                "No verified UserStatus quota state found."
            )
            if cached_snapshot and cached_snapshot.quota_windows:
                snap.quota_windows = list(cached_snapshot.quota_windows)
                snap.quota_source = cached_snapshot.quota_source
                snap.is_stale = True
                snap.stale_reason = "Showing cached model quota snapshot."
        else:
            snap.auth_state = AuthState.UNAUTHENTICATED
            snap.status_message = "No Antigravity transcripts or authenticated state found."

        return snap

    def _collect_local_transcripts(
        self, snap: ProviderSnapshot, brain_dir: Path, now: datetime
    ) -> None:
        if not brain_dir.is_dir():
            return

        cutoff_5h = now - timedelta(hours=5)
        cutoff_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        cutoff_7d = now - timedelta(days=7)
        cutoff_30d = now - timedelta(days=30)

        malformed_lines = 0
        latest_ts: Optional[datetime] = None

        sessions_5h = set()
        sessions_today = set()
        sessions_7d = set()
        sessions_30d = set()
        sessions_all = set()

        for conv_dir in sorted(brain_dir.iterdir()):
            if not conv_dir.is_dir():
                continue
            transcript_file = conv_dir / ".system_generated" / "logs" / "transcript.jsonl"
            if not transcript_file.is_file():
                continue

            session_key = conv_dir.name
            active_model_label = "Antigravity Agent Model"
            # Dedup by step_index within each conversation
            step_records: Dict[int, Tuple[Optional[datetime], str, TokenUsageStats]] = {}

            try:
                with transcript_file.open("r", encoding="utf-8", errors="replace") as fh:
                    for line_idx, raw_line in enumerate(fh):
                        line = raw_line.strip()
                        if not line:
                            continue
                        try:
                            obj = json.loads(line)
                        except json.JSONDecodeError:
                            malformed_lines += 1
                            continue
                        if not isinstance(obj, dict):
                            continue

                        # Check if user changed model selection in a step
                        content_str = str(obj.get("content") or "")
                        if "Model Selection" in content_str and " to " in content_str:
                            m = re.search(
                                r"changed setting `Model Selection` from [^\n]+? to ([^.\n]+)",
                                content_str,
                            )
                            if m:
                                active_model_label = m.group(1).strip()

                        if obj.get("source") != "MODEL":
                            continue

                        in_tok = int(obj.get("input_tokens", 0) or 0)
                        cache_tok = int(obj.get("cache_read_tokens", 0) or 0)
                        out_tok = int(obj.get("output_tokens", 0) or 0)
                        if in_tok < 0 or cache_tok < 0 or out_tok < 0:
                            continue
                        if in_tok == 0 and cache_tok == 0 and out_tok == 0:
                            continue

                        step_idx = int(obj.get("step_index", line_idx) or line_idx)
                        ts = parse_iso_timestamp(obj.get("created_at"))
                        model_name = str(obj.get("model") or active_model_label)

                        stats = TokenUsageStats(
                            input_tokens=in_tok,
                            cache_read_tokens=cache_tok,
                            output_tokens=out_tok,
                            message_count=1,
                        )
                        existing = step_records.get(step_idx)
                        if existing is None or stats.total_tokens >= existing[2].total_tokens:
                            step_records[step_idx] = (ts, model_name, stats)
            except OSError as exc:
                snap.warnings.append(sanitize_message(f"Could not read {transcript_file}: {exc}"))

            for ts, model_name, stats in step_records.values():
                sessions_all.add(session_key)
                snap.usage_all_time.add(stats)
                if model_name not in snap.model_usage_all_time:
                    snap.model_usage_all_time[model_name] = TokenUsageStats()
                snap.model_usage_all_time[model_name].add(stats)

                if ts is not None:
                    if latest_ts is None or ts > latest_ts:
                        latest_ts = ts
                    if ts >= cutoff_30d:
                        snap.usage_thirty_day.add(stats)
                        sessions_30d.add(session_key)
                    if ts >= cutoff_7d:
                        snap.usage_seven_day.add(stats)
                        sessions_7d.add(session_key)
                        if model_name not in snap.model_usage_seven_day:
                            snap.model_usage_seven_day[model_name] = TokenUsageStats()
                        snap.model_usage_seven_day[model_name].add(stats)
                    if ts >= cutoff_today:
                        snap.usage_today.add(stats)
                        sessions_today.add(session_key)
                    if ts >= cutoff_5h:
                        snap.usage_five_hour.add(stats)
                        sessions_5h.add(session_key)

        if malformed_lines > 0:
            snap.warnings.append(
                f"Skipped {malformed_lines} malformed JSONL line(s) in Antigravity transcripts."
            )

        snap.usage_five_hour.session_count = len(sessions_5h)
        snap.usage_today.session_count = len(sessions_today)
        snap.usage_seven_day.session_count = len(sessions_7d)
        snap.usage_thirty_day.session_count = len(sessions_30d)
        snap.usage_all_time.session_count = len(sessions_all)

        if latest_ts is not None:
            snap.last_activity_at = latest_ts.isoformat()

    def _try_local_language_server(
        self, snap: ProviderSnapshot, log_files: List[Path]
    ) -> bool:
        """Probe localhost HTTP port from language_server.log if accessible."""
        ports: List[int] = []
        for lg in log_files:
            try:
                text = lg.read_text(encoding="utf-8", errors="ignore")
                for m in re.finditer(
                    r"Language server listening on random port at (\d+) for HTTP\b", text
                ):
                    ports.append(int(m.group(1)))
            except OSError:
                continue

        if not ports:
            return False

        # Try the most recently logged HTTP port
        port = ports[-1]
        url = f"http://127.0.0.1:{port}/exa.language_server_pb.LanguageServerService/GetUserStatus"
        headers = {
            "Content-Type": "application/json",
            "Connect-Protocol-Version": "1",
        }
        body = {
            "metadata": {
                "ideName": "antigravity",
                "extensionName": "antigravity",
                "locale": "en",
            }
        }
        try:
            status_code, payload = self._http_post_fn(url, headers, body, 1.5)
        except Exception:
            return False

        if status_code != 200 or not isinstance(payload, dict):
            return False

        user_status = payload.get("userStatus")
        if not isinstance(user_status, dict):
            return False

        plan_status = user_status.get("planStatus")
        if isinstance(plan_status, dict):
            plan_info = plan_status.get("planInfo")
            if isinstance(plan_info, dict) and plan_info.get("planName"):
                snap.plan_name = str(plan_info["planName"])
            avail_prompt = plan_status.get("availablePromptCredits")
            if avail_prompt is not None:
                try:
                    snap.available_credits = float(avail_prompt)
                    snap.credits_label = f"Available Prompt Credits: {int(avail_prompt)}"
                except (TypeError, ValueError):
                    pass

        cascade = user_status.get("cascadeModelConfigData")
        if isinstance(cascade, dict):
            configs = cascade.get("clientModelConfigs")
            if isinstance(configs, list):
                for cfg in configs:
                    if not isinstance(cfg, dict):
                        continue
                    label = str(cfg.get("label") or "").strip()
                    qinfo = cfg.get("quotaInfo")
                    if label and isinstance(qinfo, dict):
                        rem_frac = qinfo.get("remainingFraction")
                        reset_time = qinfo.get("resetTime")
                        if rem_frac is not None:
                            rem_pct = round(float(rem_frac) * 100.0, 2)
                            used_pct = round(max(0.0, 100.0 - rem_pct), 2)
                            resets_dt = parse_iso_timestamp(reset_time)
                            snap.quota_windows.append(
                                QuotaWindow(
                                    id=label.lower().replace(" ", "_"),
                                    label=label,
                                    used_percent=used_pct,
                                    remaining_percent=rem_pct,
                                    resets_at=resets_dt.isoformat() if resets_dt else None,
                                    detail=snap.plan_name or "Antigravity Model Quota",
                                )
                            )
        return len(snap.quota_windows) > 0

    def _collect_from_state_db(self, snap: ProviderSnapshot, db_path: Path) -> bool:
        """Read-only SQLite + Protobuf inspection of state.vscdb."""
        try:
            uri = f"file:{db_path}?mode=ro"
            conn = sqlite3.connect(uri, uri=True, timeout=2.0)
        except Exception as exc:
            snap.warnings.append(sanitize_message(f"Could not open state.vscdb: {exc}"))
            return False

        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT value FROM ItemTable WHERE key = 'antigravityUnifiedStateSync.userStatus'"
            )
            row = cur.fetchone()
            if not row or not row[0]:
                return False

            raw_val = row[0]
            outer_bytes = base64.b64decode(raw_val)
            # Extract nested base64-encoded UserStatus protobuf payload
            b64_matches = re.findall(b"[A-Za-z0-9+/=]{80,}", outer_bytes)
            if not b64_matches:
                return False

            inner_bytes: Optional[bytes] = None
            for candidate in b64_matches:
                try:
                    decoded = base64.b64decode(candidate)
                    if len(decoded) > 30:
                        inner_bytes = decoded
                        break
                except Exception:
                    continue

            if not inner_bytes:
                return False

            self._parse_user_status_protobuf(snap, inner_bytes)

            # Also check modelCredits if available
            cur.execute(
                "SELECT value FROM ItemTable WHERE key = 'antigravityUnifiedStateSync.modelCredits'"
            )
            mc_row = cur.fetchone()
            if mc_row and mc_row[0] and snap.available_credits is None:
                self._parse_model_credits_protobuf(snap, mc_row[0])

            return len(snap.quota_windows) > 0 or bool(snap.plan_name)
        except Exception as exc:
            snap.warnings.append(sanitize_message(f"Error decoding state.vscdb: {exc}"))
            return False
        finally:
            conn.close()

    def _parse_user_status_protobuf(
        self, snap: ProviderSnapshot, inner_bytes: bytes
    ) -> None:
        top_fields = decode_protobuf_fields(inner_bytes)
        for fnum, wtype, val in top_fields:
            # Never read Field 3 (name) or Field 7 (email) into logs
            if fnum == 36 and wtype == "bytes":
                # Plan info block
                for sf, sw, sv in decode_protobuf_fields(val):
                    if sf == 2 and sw == "bytes":
                        try:
                            snap.plan_name = sv.decode("utf-8").strip()
                        except Exception:
                            pass
            elif fnum == 38 and wtype == "bytes":
                # Available credits info
                for sf, sw, sv in decode_protobuf_fields(val):
                    if sf == 13 and sw == "varint":
                        snap.available_credits = float(sv)
                        snap.credits_label = f"Available Model Credits: {sv}"
            elif fnum == 33 and wtype == "bytes":
                # cascadeModelConfigData -> clientModelConfigs (Subfield 1)
                for sf, sw, sv in decode_protobuf_fields(val):
                    if sf != 1 or sw != "bytes":
                        continue
                    model_label: Optional[str] = None
                    rem_fraction: Optional[float] = None
                    reset_epoch: Optional[int] = None

                    for mf, mw, mv in decode_protobuf_fields(sv):
                        if mf == 1 and mw == "bytes":
                            try:
                                model_label = mv.decode("utf-8").strip()
                            except Exception:
                                pass
                        elif mf == 15 and mw == "bytes":
                            for qf, qw, qv in decode_protobuf_fields(mv):
                                if qf == 1 and qw == "float32":
                                    rem_fraction = float(qv)
                                elif qf == 2 and qw == "bytes":
                                    for tf, tw, tv in decode_protobuf_fields(qv):
                                        if tf == 1 and tw == "varint":
                                            reset_epoch = int(tv)

                    if model_label and rem_fraction is not None:
                        rem_pct = round(max(0.0, min(100.0, rem_fraction * 100.0)), 2)
                        used_pct = round(max(0.0, 100.0 - rem_pct), 2)
                        resets_iso: Optional[str] = None
                        if reset_epoch:
                            try:
                                resets_iso = datetime.fromtimestamp(
                                    reset_epoch, tz=timezone.utc
                                ).isoformat()
                            except (OSError, OverflowError, ValueError):
                                resets_iso = None
                        snap.quota_windows.append(
                            QuotaWindow(
                                id=model_label.lower().replace(" ", "_"),
                                label=model_label,
                                used_percent=used_pct,
                                remaining_percent=rem_pct,
                                resets_at=resets_iso,
                                detail=snap.plan_name or "Antigravity Model Pool",
                            )
                        )

    def _parse_model_credits_protobuf(
        self, snap: ProviderSnapshot, raw_b64: str | bytes
    ) -> None:
        try:
            outer = base64.b64decode(raw_b64)
            for fnum, wtype, val in decode_protobuf_fields(outer):
                if fnum == 1 and wtype == "bytes":
                    sub = decode_protobuf_fields(val)
                    key_name = ""
                    b64_val = b""
                    for sf, sw, sv in sub:
                        if sf == 1 and sw == "bytes":
                            key_name = sv.decode("utf-8", errors="ignore")
                        elif sf == 2 and sw == "bytes":
                            for s2f, s2w, s2v in decode_protobuf_fields(sv):
                                if s2f == 1 and s2w == "bytes":
                                    b64_val = s2v
                    if key_name == "availableCreditsSentinelKey" and b64_val:
                        inner = base64.b64decode(b64_val)
                        for inf, inw, inv in decode_protobuf_fields(inner):
                            if inf == 2 and inw == "varint":
                                snap.available_credits = float(inv)
                                snap.credits_label = f"Available Model Credits: {inv}"
        except Exception:
            pass
