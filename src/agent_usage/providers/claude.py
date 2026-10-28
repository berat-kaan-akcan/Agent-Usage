"""Claude Code local session history parser and verified subscription quota collector."""

from __future__ import annotations

import json
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


class ClaudeCollector(BaseProviderCollector):
    """Collects recorded local Claude Code usage and verified Anthropic OAuth subscription quotas."""

    provider_id = "claude"
    display_name = "Claude Code"
    OAUTH_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"

    def __init__(
        self,
        paths: Optional[ProviderPaths] = None,
        http_get_fn: Optional[Callable[[str, Dict[str, str], float], Tuple[int, Dict[str, Any]]]] = None,
    ) -> None:
        self.paths = paths or discover_provider_paths()
        self._http_get_fn = http_get_fn or self._default_http_get

    @staticmethod
    def _default_http_get(
        url: str, headers: Dict[str, str], timeout: float = 5.0
    ) -> Tuple[int, Dict[str, Any]]:
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status = resp.getcode()
                body = resp.read().decode("utf-8", errors="replace")
                return status, json.loads(body) if body.strip() else {}
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
                "Local tokens reflect recorded Claude Code CLI/IDE sessions in ~/.claude/projects. "
                "Account 5-hour and 7-day subscription limits are shared across Claude Code, "
                "Claude Web (claude.ai), and Claude Desktop."
            ),
            unsupported_notes=[
                "Individual Claude Web (claude.ai) and Claude Desktop conversation token counts "
                "are not exposed locally and are never fabricated."
            ],
        )

        claude_dir = self.paths.claude_dir
        projects_dir = self.paths.claude_projects_dir
        creds_file = self.paths.claude_credentials_file

        has_dir = claude_dir.is_dir()
        has_projects = projects_dir.is_dir()
        has_creds = creds_file.is_file()

        if has_dir:
            snap.discovered_paths.append(str(claude_dir))
        if has_projects:
            snap.discovered_paths.append(str(projects_dir))
        if has_creds:
            snap.discovered_paths.append(str(creds_file))

        if not has_dir and not has_projects and not has_creds:
            snap.auth_state = AuthState.NOT_INSTALLED
            snap.status_message = (
                f"Claude Code directory not found at {claude_dir}. "
                "Install and run Claude Code to record local sessions."
            )
            return snap

        # 1. Parse local session JSONL files
        self._collect_local_sessions(snap, projects_dir, current_time)

        # 2. Read credentials and fetch verified subscription quota if available
        if not has_creds:
            if snap.usage_all_time.message_count > 0:
                snap.auth_state = AuthState.LOCAL_ONLY
                snap.status_message = (
                    "Local Claude Code session history loaded. "
                    "No OAuth credentials (.credentials.json) found for live subscription quota."
                )
            else:
                snap.auth_state = AuthState.UNAUTHENTICATED
                snap.status_message = "No Claude Code credentials or session logs found."
            return snap

        self._collect_oauth_quota(
            snap,
            creds_file=creds_file,
            allow_network=allow_network,
            now=current_time,
            cached_snapshot=cached_snapshot,
        )
        return snap

    def _collect_local_sessions(
        self, snap: ProviderSnapshot, projects_dir: Path, now: datetime
    ) -> None:
        if not projects_dir.is_dir():
            return

        cutoff_5h = now - timedelta(hours=5)
        cutoff_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        cutoff_7d = now - timedelta(days=7)
        cutoff_30d = now - timedelta(days=30)

        # Deduplicate streaming assistant chunks across session files by unique key
        # Key -> (timestamp, model, session_file_str, TokenUsageStats)
        deduped_messages: Dict[str, Tuple[Optional[datetime], str, str, TokenUsageStats]] = {}
        malformed_lines = 0
        latest_ts: Optional[datetime] = None

        jsonl_files = sorted(projects_dir.rglob("*.jsonl"))
        for jfile in jsonl_files:
            if not jfile.is_file():
                continue
            session_key = str(jfile)
            try:
                with jfile.open("r", encoding="utf-8", errors="replace") as fh:
                    for line_idx, raw_line in enumerate(fh):
                        line = raw_line.strip()
                        if not line:
                            continue
                        try:
                            record = json.loads(line)
                        except json.JSONDecodeError:
                            malformed_lines += 1
                            continue
                        if not isinstance(record, dict):
                            continue

                        rec_type = record.get("type")
                        msg = record.get("message")
                        if rec_type != "assistant" and not (
                            isinstance(msg, dict) and msg.get("role") == "assistant"
                        ):
                            continue
                        if not isinstance(msg, dict):
                            continue
                        usage = msg.get("usage")
                        if not isinstance(usage, dict):
                            continue

                        in_tok = int(usage.get("input_tokens", 0) or 0)
                        cache_create = int(usage.get("cache_creation_input_tokens", 0) or 0)
                        cache_read = int(usage.get("cache_read_input_tokens", 0) or 0)
                        out_tok = int(usage.get("output_tokens", 0) or 0)
                        if in_tok < 0 or cache_create < 0 or cache_read < 0 or out_tok < 0:
                            continue

                        msg_id = (
                            msg.get("id")
                            or record.get("requestId")
                            or record.get("uuid")
                            or f"{session_key}:{line_idx}"
                        )
                        dedup_key = f"{session_key}::{msg_id}"
                        model_name = str(msg.get("model") or record.get("model") or "claude-unknown")
                        ts = parse_iso_timestamp(record.get("timestamp") or msg.get("timestamp"))

                        stats = TokenUsageStats(
                            input_tokens=in_tok,
                            cache_creation_tokens=cache_create,
                            cache_read_tokens=cache_read,
                            output_tokens=out_tok,
                            message_count=1,
                        )

                        # Keep the record with the highest output/total tokens for streaming updates
                        existing = deduped_messages.get(dedup_key)
                        if existing is None or stats.total_tokens >= existing[3].total_tokens:
                            deduped_messages[dedup_key] = (ts, model_name, session_key, stats)
            except OSError as exc:
                snap.warnings.append(sanitize_message(f"Could not read {jfile.name}: {exc}"))

        if malformed_lines > 0:
            snap.warnings.append(f"Skipped {malformed_lines} malformed JSONL line(s) in Claude logs.")

        sessions_5h = set()
        sessions_today = set()
        sessions_7d = set()
        sessions_30d = set()
        sessions_all = set()

        for ts, model_name, session_key, stats in deduped_messages.values():
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

        snap.usage_five_hour.session_count = len(sessions_5h)
        snap.usage_today.session_count = len(sessions_today)
        snap.usage_seven_day.session_count = len(sessions_7d)
        snap.usage_thirty_day.session_count = len(sessions_30d)
        snap.usage_all_time.session_count = len(sessions_all)

        if latest_ts is not None:
            snap.last_activity_at = latest_ts.isoformat()

    def _collect_oauth_quota(
        self,
        snap: ProviderSnapshot,
        creds_file: Path,
        allow_network: bool,
        now: datetime,
        cached_snapshot: Optional[ProviderSnapshot],
    ) -> None:
        try:
            creds_raw = json.loads(creds_file.read_text(encoding="utf-8"))
        except Exception as exc:
            snap.auth_state = (
                AuthState.LOCAL_ONLY
                if snap.usage_all_time.message_count > 0
                else AuthState.ERROR
            )
            snap.status_message = sanitize_message(f"Invalid credentials JSON: {exc}")
            return

        oauth = creds_raw.get("claudeAiOauth") if isinstance(creds_raw, dict) else None
        if not isinstance(oauth, dict):
            snap.auth_state = (
                AuthState.LOCAL_ONLY
                if snap.usage_all_time.message_count > 0
                else AuthState.UNAUTHENTICATED
            )
            snap.status_message = "No claudeAiOauth block found in .credentials.json."
            return

        sub_type = str(oauth.get("subscriptionType") or "").strip()
        rate_tier = str(oauth.get("rateLimitTier") or "").strip()
        if sub_type:
            snap.plan_name = f"Claude {sub_type.capitalize()}"
            snap.billing_mode = "subscription"
        elif rate_tier:
            snap.plan_name = rate_tier
            snap.billing_mode = "subscription"

        access_token = str(oauth.get("accessToken") or "").strip()
        expires_at = parse_iso_timestamp(oauth.get("expiresAt"))

        if not access_token:
            snap.auth_state = AuthState.UNAUTHENTICATED
            snap.status_message = "Claude OAuth access token is missing."
            return

        if expires_at is not None and expires_at <= now:
            snap.auth_state = AuthState.EXPIRED
            snap.status_message = (
                f"Claude OAuth token expired at {expires_at.strftime('%Y-%m-%d %H:%M UTC')}. "
                "Run `claude` in terminal to refresh authentication."
            )
            self._apply_cached_quotas_if_available(
                snap, cached_snapshot, reason="OAuth token expired; showing cached quota."
            )
            return

        if not allow_network:
            snap.auth_state = AuthState.LOCAL_ONLY
            snap.status_message = "Network quota polling disabled; showing local session usage."
            self._apply_cached_quotas_if_available(
                snap, cached_snapshot, reason="Network polling disabled; showing cached quota."
            )
            return

        headers = {
            "Authorization": f"Bearer {access_token}",
            "anthropic-beta": "oauth-2025-04-20",
            "User-Agent": "claude-code/1.0.35",
            "Accept": "application/json",
        }

        try:
            status_code, payload = self._http_get_fn(self.OAUTH_USAGE_URL, headers, 5.0)
        except Exception as exc:
            snap.auth_state = AuthState.LOCAL_ONLY
            snap.status_message = sanitize_message(f"Quota API unreachable: {exc}")
            self._apply_cached_quotas_if_available(
                snap, cached_snapshot, reason="Network error; showing last cached quota."
            )
            return

        if status_code in (401, 403):
            snap.auth_state = AuthState.EXPIRED
            snap.status_message = "Claude OAuth token rejected (HTTP 401/403). Please re-authenticate."
            self._apply_cached_quotas_if_available(
                snap, cached_snapshot, reason="Authentication expired; showing cached quota."
            )
            return

        if status_code == 429:
            snap.auth_state = AuthState.RATE_LIMITED
            snap.status_message = "Anthropic usage endpoint rate-limited (HTTP 429)."
            self._apply_cached_quotas_if_available(
                snap, cached_snapshot, reason="Rate limited (HTTP 429); showing cached quota."
            )
            return

        if status_code != 200 or not isinstance(payload, dict):
            snap.auth_state = AuthState.LOCAL_ONLY
            snap.status_message = f"Unexpected response from Claude usage API (HTTP {status_code})."
            self._apply_cached_quotas_if_available(
                snap, cached_snapshot, reason=f"API HTTP {status_code}; showing cached quota."
            )
            return

        # Parse verified windows from Anthropic OAuth usage response
        window_specs = [
            ("five_hour", "5-Hour Window", 300),
            ("seven_day", "7-Day All Models", 10080),
            ("seven_day_sonnet", "7-Day Sonnet", 10080),
            ("seven_day_opus", "7-Day Opus", 10080),
        ]
        for key, label, win_mins in window_specs:
            win_obj = payload.get(key)
            if isinstance(win_obj, dict) and win_obj.get("utilization") is not None:
                try:
                    used_pct = float(win_obj["utilization"])
                    resets_dt = parse_iso_timestamp(win_obj.get("resets_at"))
                    snap.quota_windows.append(
                        QuotaWindow(
                            id=key,
                            label=label,
                            used_percent=used_pct,
                            resets_at=resets_dt.isoformat() if resets_dt else None,
                            window_minutes=win_mins,
                            detail="Shared across Claude Code, Web & Desktop",
                        )
                    )
                except (TypeError, ValueError):
                    pass

        extra = payload.get("extra_usage")
        if isinstance(extra, dict) and extra.get("is_enabled"):
            used_credits = extra.get("used_credits")
            monthly_limit = extra.get("monthly_limit")
            if used_credits is not None and monthly_limit is not None:
                snap.credits_label = f"Extra Usage: ${used_credits} / ${monthly_limit}"
            elif extra.get("utilization") is not None:
                snap.quota_windows.append(
                    QuotaWindow(
                        id="extra_usage",
                        label="Extra Usage Credits",
                        used_percent=float(extra["utilization"]),
                    )
                )

        snap.auth_state = AuthState.AUTHENTICATED
        snap.quota_source = QuotaSourceType.OAUTH_API
        snap.status_message = "Verified subscription quota and local history active."

    @staticmethod
    def _apply_cached_quotas_if_available(
        snap: ProviderSnapshot,
        cached_snapshot: Optional[ProviderSnapshot],
        reason: str,
    ) -> None:
        if cached_snapshot and cached_snapshot.quota_windows:
            snap.quota_windows = list(cached_snapshot.quota_windows)
            snap.quota_source = cached_snapshot.quota_source
            snap.is_stale = True
            snap.stale_reason = reason
