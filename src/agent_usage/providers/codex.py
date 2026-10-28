"""OpenAI Codex CLI local session collector and verified account quota retriever."""

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


class CodexCollector(BaseProviderCollector):
    """Collects recorded OpenAI Codex local session usage and verified account-level quotas."""

    provider_id = "codex"
    display_name = "OpenAI Codex"
    WHAM_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"

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
                "Local tokens reflect recorded Codex CLI rollout logs in ~/.codex/sessions. "
                "Distinguishes ChatGPT subscription rate limits (5h/7d windows) from OpenAI API key billing."
            ),
            unsupported_notes=[
                "When using OPENAI_API_KEY billing mode, OpenAI does not expose fixed 5-hour/7-day "
                "subscription percentages; usage is billed per token via OpenAI Platform."
            ],
        )

        codex_dir = self.paths.codex_dir
        sessions_dir = self.paths.codex_sessions_dir
        auth_file = self.paths.codex_auth_file

        has_dir = codex_dir.is_dir()
        has_sessions = sessions_dir.is_dir()
        has_auth = auth_file.is_file()

        if has_dir:
            snap.discovered_paths.append(str(codex_dir))
        if has_sessions:
            snap.discovered_paths.append(str(sessions_dir))
        if has_auth:
            snap.discovered_paths.append(str(auth_file))

        if not has_dir and not has_sessions and not has_auth:
            snap.auth_state = AuthState.NOT_INSTALLED
            snap.status_message = (
                f"OpenAI Codex directory not found at {codex_dir}. "
                "Install and run Codex CLI to record local sessions."
            )
            return snap

        # 1. Parse local session rollout JSONL logs and extract any embedded rate_limits snapshot
        latest_embedded_rl, latest_rl_ts = self._collect_local_sessions(
            snap, sessions_dir, current_time
        )

        # 2. Check auth.json for ChatGPT OAuth vs API Key billing
        if has_auth:
            self._collect_auth_and_quota(
                snap,
                auth_file=auth_file,
                allow_network=allow_network,
                now=current_time,
                embedded_rate_limits=latest_embedded_rl,
                embedded_rl_ts=latest_rl_ts,
                cached_snapshot=cached_snapshot,
            )
        elif latest_embedded_rl is not None:
            self._apply_embedded_rate_limits(snap, latest_embedded_rl, latest_rl_ts, current_time)
            snap.auth_state = AuthState.LOCAL_ONLY
            snap.status_message = (
                "Loaded local Codex session usage and embedded rate-limit snapshot (no auth.json)."
            )
        elif snap.usage_all_time.message_count > 0:
            snap.auth_state = AuthState.LOCAL_ONLY
            snap.status_message = "Loaded local Codex session logs (no auth.json found)."
        else:
            snap.auth_state = AuthState.UNAUTHENTICATED
            snap.status_message = "No Codex credentials or session logs found."

        return snap

    def _collect_local_sessions(
        self, snap: ProviderSnapshot, sessions_dir: Path, now: datetime
    ) -> Tuple[Optional[Dict[str, Any]], Optional[datetime]]:
        if not sessions_dir.is_dir():
            return None, None

        cutoff_5h = now - timedelta(hours=5)
        cutoff_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        cutoff_7d = now - timedelta(days=7)
        cutoff_30d = now - timedelta(days=30)

        malformed_lines = 0
        latest_activity: Optional[datetime] = None
        latest_rate_limits: Optional[Dict[str, Any]] = None
        latest_rate_limits_ts: Optional[datetime] = None

        sessions_5h = set()
        sessions_today = set()
        sessions_7d = set()
        sessions_30d = set()
        sessions_all = set()

        jsonl_files = sorted(sessions_dir.rglob("*.jsonl"))
        for jfile in jsonl_files:
            if not jfile.is_file():
                continue
            session_key = str(jfile)
            session_model = "codex-default"
            prev_cumulative: Optional[Tuple[int, int, int, int]] = None
            file_events: List[Tuple[Optional[datetime], str, TokenUsageStats]] = []

            try:
                with jfile.open("r", encoding="utf-8", errors="replace") as fh:
                    for raw_line in fh:
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

                        ts = parse_iso_timestamp(record.get("timestamp"))
                        rec_type = record.get("type")
                        payload = record.get("payload")

                        # Detect model name from session metadata or turn context
                        if isinstance(payload, dict):
                            if payload.get("model"):
                                session_model = str(payload["model"])
                            elif isinstance(payload.get("info"), dict) and payload["info"].get("model"):
                                session_model = str(payload["info"]["model"])
                        if record.get("model"):
                            session_model = str(record["model"])

                        if rec_type != "event_msg" or not isinstance(payload, dict):
                            continue
                        if payload.get("type") != "token_count":
                            continue

                        # Check embedded rate_limits
                        rl = payload.get("rate_limits")
                        if isinstance(rl, dict):
                            if (
                                latest_rate_limits is None
                                or (ts is not None and (latest_rate_limits_ts is None or ts >= latest_rate_limits_ts))
                            ):
                                latest_rate_limits = rl
                                latest_rate_limits_ts = ts

                        info = payload.get("info")
                        if not isinstance(info, dict):
                            continue

                        tot_obj = info.get("total_token_usage")
                        last_obj = info.get("last_token_usage")

                        delta_stats: Optional[TokenUsageStats] = None
                        if isinstance(tot_obj, dict):
                            cur_in = int(tot_obj.get("input_tokens", 0) or 0)
                            cur_cache = int(tot_obj.get("cached_input_tokens", 0) or 0)
                            cur_out = int(tot_obj.get("output_tokens", 0) or 0)
                            cur_reason = int(tot_obj.get("reasoning_output_tokens", 0) or 0)
                            cur_tuple = (cur_in, cur_cache, cur_out, cur_reason)
                            if prev_cumulative == cur_tuple:
                                # Duplicate cumulative snapshot; skip
                                continue
                            if prev_cumulative is not None and all(
                                c >= p for c, p in zip(cur_tuple, prev_cumulative)
                            ):
                                delta_stats = TokenUsageStats(
                                    input_tokens=max(0, (cur_in - prev_cumulative[0]) - (cur_cache - prev_cumulative[1])),
                                    cache_read_tokens=max(0, cur_cache - prev_cumulative[1]),
                                    output_tokens=max(0, cur_out - prev_cumulative[2]),
                                    reasoning_output_tokens=max(0, cur_reason - prev_cumulative[3]),
                                    message_count=1,
                                )
                            else:
                                delta_stats = TokenUsageStats(
                                    input_tokens=max(0, cur_in - cur_cache),
                                    cache_read_tokens=max(0, cur_cache),
                                    output_tokens=max(0, cur_out),
                                    reasoning_output_tokens=max(0, cur_reason),
                                    message_count=1,
                                )
                            prev_cumulative = cur_tuple
                        elif isinstance(last_obj, dict):
                            l_in = int(last_obj.get("input_tokens", 0) or 0)
                            l_cache = int(last_obj.get("cached_input_tokens", 0) or 0)
                            l_out = int(last_obj.get("output_tokens", 0) or 0)
                            l_reason = int(last_obj.get("reasoning_output_tokens", 0) or 0)
                            delta_stats = TokenUsageStats(
                                input_tokens=max(0, l_in - l_cache),
                                cache_read_tokens=max(0, l_cache),
                                output_tokens=max(0, l_out),
                                reasoning_output_tokens=max(0, l_reason),
                                message_count=1,
                            )

                        if delta_stats is not None and (
                            delta_stats.total_tokens > 0 or delta_stats.reasoning_output_tokens > 0
                        ):
                            file_events.append((ts, session_model, delta_stats))
            except OSError as exc:
                snap.warnings.append(sanitize_message(f"Could not read {jfile.name}: {exc}"))

            for ts, model_name, stats in file_events:
                sessions_all.add(session_key)
                snap.usage_all_time.add(stats)
                if model_name not in snap.model_usage_all_time:
                    snap.model_usage_all_time[model_name] = TokenUsageStats()
                snap.model_usage_all_time[model_name].add(stats)

                if ts is not None:
                    if latest_activity is None or ts > latest_activity:
                        latest_activity = ts
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
            snap.warnings.append(f"Skipped {malformed_lines} malformed JSONL line(s) in Codex logs.")

        snap.usage_five_hour.session_count = len(sessions_5h)
        snap.usage_today.session_count = len(sessions_today)
        snap.usage_seven_day.session_count = len(sessions_7d)
        snap.usage_thirty_day.session_count = len(sessions_30d)
        snap.usage_all_time.session_count = len(sessions_all)

        if latest_activity is not None:
            snap.last_activity_at = latest_activity.isoformat()

        return latest_rate_limits, latest_rate_limits_ts

    def _collect_auth_and_quota(
        self,
        snap: ProviderSnapshot,
        auth_file: Path,
        allow_network: bool,
        now: datetime,
        embedded_rate_limits: Optional[Dict[str, Any]],
        embedded_rl_ts: Optional[datetime],
        cached_snapshot: Optional[ProviderSnapshot],
    ) -> None:
        try:
            auth_data = json.loads(auth_file.read_text(encoding="utf-8"))
        except Exception as exc:
            snap.auth_state = (
                AuthState.LOCAL_ONLY
                if snap.usage_all_time.message_count > 0
                else AuthState.ERROR
            )
            snap.status_message = sanitize_message(f"Invalid Codex auth.json: {exc}")
            return

        if not isinstance(auth_data, dict):
            snap.auth_state = AuthState.ERROR
            snap.status_message = "Malformed Codex auth.json structure."
            return

        auth_mode = str(auth_data.get("auth_mode") or "").lower()
        api_key = auth_data.get("OPENAI_API_KEY")
        tokens = auth_data.get("tokens") if isinstance(auth_data.get("tokens"), dict) else {}
        access_token = str(tokens.get("access_token") or "").strip()
        account_id = str(tokens.get("account_id") or "").strip()

        # Distinguish API billing from ChatGPT subscription quotas
        if (auth_mode in ("api_key", "apikey") or (api_key and not access_token)):
            snap.auth_state = AuthState.AUTHENTICATED
            snap.billing_mode = "api_billing"
            snap.plan_name = "OpenAI Platform API (Pay-per-token)"
            snap.quota_source = QuotaSourceType.API_BILLING
            snap.status_message = (
                "Authenticated via OpenAI API Key (pay-as-you-go billing; "
                "no fixed 5h/7d subscription window applies)."
            )
            return

        snap.billing_mode = "subscription"
        if not access_token:
            if embedded_rate_limits is not None:
                self._apply_embedded_rate_limits(snap, embedded_rate_limits, embedded_rl_ts, now)
                snap.auth_state = AuthState.LOCAL_ONLY
                snap.status_message = "Using embedded session rate-limit snapshot (no OAuth token)."
            else:
                snap.auth_state = AuthState.UNAUTHENTICATED
                snap.status_message = "No ChatGPT OAuth access token or API key in auth.json."
            return

        if allow_network:
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
                "User-Agent": "codex-cli/0.1.0",
            }
            if account_id:
                headers["ChatGPT-Account-Id"] = account_id

            try:
                status_code, payload = self._http_get_fn(self.WHAM_USAGE_URL, headers, 5.0)
                if status_code == 200 and isinstance(payload, dict):
                    self._parse_wham_payload(snap, payload)
                    snap.auth_state = AuthState.AUTHENTICATED
                    snap.quota_source = QuotaSourceType.OAUTH_API
                    snap.status_message = "Verified ChatGPT subscription quota and local history active."
                    return
                if status_code in (401, 403):
                    snap.auth_state = AuthState.EXPIRED
                    snap.status_message = "Codex ChatGPT OAuth token expired (HTTP 401/403)."
                elif status_code == 429:
                    snap.auth_state = AuthState.RATE_LIMITED
                    snap.status_message = "Codex usage endpoint rate-limited (HTTP 429)."
                else:
                    snap.auth_state = AuthState.LOCAL_ONLY
                    snap.status_message = f"Codex quota endpoint returned HTTP {status_code}."
            except Exception as exc:
                snap.auth_state = AuthState.LOCAL_ONLY
                snap.status_message = sanitize_message(f"Live quota endpoint unreachable: {exc}")
        else:
            snap.auth_state = AuthState.LOCAL_ONLY
            snap.status_message = "Network quota polling disabled; using local session records."

        # Fallback 1: Embedded rate_limits in rollout JSONL
        if embedded_rate_limits is not None:
            self._apply_embedded_rate_limits(snap, embedded_rate_limits, embedded_rl_ts, now)
            return

        # Fallback 2: Cached snapshot
        if cached_snapshot and cached_snapshot.quota_windows:
            snap.quota_windows = list(cached_snapshot.quota_windows)
            snap.quota_source = cached_snapshot.quota_source
            snap.is_stale = True
            snap.stale_reason = "Showing cached quota snapshot."

    def _parse_wham_payload(self, snap: ProviderSnapshot, payload: Dict[str, Any]) -> None:
        plan_type = str(payload.get("plan_type") or "").strip()
        if plan_type:
            snap.plan_name = f"ChatGPT {plan_type.capitalize()}"
        rl = payload.get("rate_limit")
        if isinstance(rl, dict):
            prim = rl.get("primary_window")
            if isinstance(prim, dict) and prim.get("used_percent") is not None:
                resets_dt = parse_iso_timestamp(prim.get("reset_at"))
                win_sec = int(prim.get("limit_window_seconds", 18000) or 18000)
                snap.quota_windows.append(
                    QuotaWindow(
                        id="primary_window",
                        label="5-Hour Window",
                        used_percent=float(prim["used_percent"]),
                        resets_at=resets_dt.isoformat() if resets_dt else None,
                        window_minutes=win_sec // 60,
                        detail=f"ChatGPT {plan_type.capitalize() or 'Subscription'}",
                    )
                )
            sec = rl.get("secondary_window")
            if isinstance(sec, dict) and sec.get("used_percent") is not None:
                resets_dt = parse_iso_timestamp(sec.get("reset_at"))
                win_sec = int(sec.get("limit_window_seconds", 604800) or 604800)
                snap.quota_windows.append(
                    QuotaWindow(
                        id="secondary_window",
                        label="7-Day Window",
                        used_percent=float(sec["used_percent"]),
                        resets_at=resets_dt.isoformat() if resets_dt else None,
                        window_minutes=win_sec // 60,
                        detail=f"ChatGPT {plan_type.capitalize() or 'Subscription'}",
                    )
                )
        credits_obj = payload.get("credits")
        if isinstance(credits_obj, dict) and credits_obj.get("has_credits"):
            bal = credits_obj.get("balance")
            if bal is not None:
                snap.credits_label = f"Credits Balance: {bal}"

    def _apply_embedded_rate_limits(
        self,
        snap: ProviderSnapshot,
        rl: Dict[str, Any],
        rl_ts: Optional[datetime],
        now: datetime,
    ) -> None:
        plan_type = str(rl.get("plan_type") or "").strip()
        if plan_type and not snap.plan_name:
            snap.plan_name = f"ChatGPT {plan_type.capitalize()}"
        snap.billing_mode = "subscription"
        snap.quota_source = QuotaSourceType.LOCAL_EVENT_SNAPSHOT

        for key, default_label, default_mins in [
            ("primary", "5-Hour Window", 300),
            ("secondary", "7-Day Window", 10080),
        ]:
            win = rl.get(key)
            if isinstance(win, dict) and win.get("used_percent") is not None:
                resets_dt = parse_iso_timestamp(win.get("resets_at"))
                win_mins = int(win.get("window_minutes", default_mins) or default_mins)
                snap.quota_windows.append(
                    QuotaWindow(
                        id=key,
                        label=default_label,
                        used_percent=float(win["used_percent"]),
                        resets_at=resets_dt.isoformat() if resets_dt else None,
                        window_minutes=win_mins,
                        detail="Recorded from Codex session rate_limits event",
                    )
                )

        credits_obj = rl.get("credits")
        if isinstance(credits_obj, dict) and credits_obj.get("has_credits"):
            bal = credits_obj.get("balance")
            if bal is not None:
                snap.credits_label = f"Credits Balance: {bal}"

        if rl_ts is not None and (now - rl_ts) > timedelta(hours=1):
            snap.is_stale = True
            snap.stale_reason = (
                f"Embedded rate-limit snapshot is from {rl_ts.strftime('%Y-%m-%d %H:%M UTC')}."
            )
