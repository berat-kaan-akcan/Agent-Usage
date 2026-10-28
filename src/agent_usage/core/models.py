"""Data models for local token activity and verified account-level quotas."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


class AuthState(str, Enum):
    """Authentication and data availability state for a provider."""

    AUTHENTICATED = "authenticated"
    LOCAL_ONLY = "local_only"
    EXPIRED = "expired"
    RATE_LIMITED = "rate_limited"
    UNAUTHENTICATED = "unauthenticated"
    NOT_INSTALLED = "not_installed"
    ERROR = "error"


class QuotaSourceType(str, Enum):
    """How the account-level quota was verified."""

    OAUTH_API = "oauth_api"
    LOCAL_EVENT_SNAPSHOT = "local_event_snapshot"
    LOCAL_LANGUAGE_SERVER = "local_language_server"
    LOCAL_STATE_DB = "local_state_db"
    API_BILLING = "api_billing"
    UNAVAILABLE = "unavailable"


@dataclass
class TokenUsageStats:
    """Recorded token counts from local session history."""

    input_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    output_tokens: int = 0
    reasoning_output_tokens: int = 0
    message_count: int = 0
    session_count: int = 0

    @property
    def total_tokens(self) -> int:
        """Total tokens across input, cache creation, cache read, and output."""
        return (
            self.input_tokens
            + self.cache_creation_tokens
            + self.cache_read_tokens
            + self.output_tokens
        )

    @property
    def active_tokens(self) -> int:
        """Non-cached input + output tokens."""
        return self.input_tokens + self.output_tokens

    def add(self, other: "TokenUsageStats") -> None:
        """Accumulate another TokenUsageStats into this instance."""
        self.input_tokens += other.input_tokens
        self.cache_creation_tokens += other.cache_creation_tokens
        self.cache_read_tokens += other.cache_read_tokens
        self.output_tokens += other.output_tokens
        self.reasoning_output_tokens += other.reasoning_output_tokens
        self.message_count += other.message_count
        self.session_count += other.session_count

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["total_tokens"] = self.total_tokens
        data["active_tokens"] = self.active_tokens
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TokenUsageStats":
        return cls(
            input_tokens=int(data.get("input_tokens", 0) or 0),
            cache_creation_tokens=int(data.get("cache_creation_tokens", 0) or 0),
            cache_read_tokens=int(data.get("cache_read_tokens", 0) or 0),
            output_tokens=int(data.get("output_tokens", 0) or 0),
            reasoning_output_tokens=int(data.get("reasoning_output_tokens", 0) or 0),
            message_count=int(data.get("message_count", 0) or 0),
            session_count=int(data.get("session_count", 0) or 0),
        )


@dataclass
class QuotaWindow:
    """A verified account-level or model-level quota window.

    Never fabricated or inferred from local token counts.
    """

    id: str
    label: str
    used_percent: Optional[float] = None
    remaining_percent: Optional[float] = None
    resets_at: Optional[str] = None  # ISO 8601 UTC string
    window_minutes: Optional[int] = None
    detail: Optional[str] = None
    is_exhausted: bool = False

    def __post_init__(self) -> None:
        if self.used_percent is not None and self.remaining_percent is None:
            self.used_percent = max(0.0, min(100.0, float(self.used_percent)))
            self.remaining_percent = round(max(0.0, 100.0 - self.used_percent), 2)
        elif self.remaining_percent is not None and self.used_percent is None:
            self.remaining_percent = max(0.0, min(100.0, float(self.remaining_percent)))
            self.used_percent = round(max(0.0, 100.0 - self.remaining_percent), 2)
        if self.used_percent is not None and self.used_percent >= 100.0:
            self.is_exhausted = True

    def time_until_reset_human(self, now: Optional[datetime] = None) -> Optional[str]:
        """Format human-readable countdown until reset time."""
        if not self.resets_at:
            return None
        try:
            target = datetime.fromisoformat(self.resets_at.replace("Z", "+00:00"))
            if target.tzinfo is None:
                target = target.replace(tzinfo=timezone.utc)
            current = now or datetime.now(timezone.utc)
            delta_sec = int((target - current).total_seconds())
            if delta_sec <= 0:
                return "Resetting now"
            days, rem = divmod(delta_sec, 86400)
            hours, rem = divmod(rem, 3600)
            minutes, _ = divmod(rem, 60)
            if days > 0:
                return f"{days}d {hours}h"
            if hours > 0:
                return f"{hours}h {minutes}m"
            return f"{minutes}m"
        except Exception:
            return None

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["reset_countdown"] = self.time_until_reset_human()
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "QuotaWindow":
        return cls(
            id=str(data.get("id", "")),
            label=str(data.get("label", "")),
            used_percent=data.get("used_percent"),
            remaining_percent=data.get("remaining_percent"),
            resets_at=data.get("resets_at"),
            window_minutes=data.get("window_minutes"),
            detail=data.get("detail"),
            is_exhausted=bool(data.get("is_exhausted", False)),
        )


@dataclass
class ProviderSnapshot:
    """Complete snapshot of a single AI provider's local usage and verified quotas."""

    provider_id: str  # "claude", "codex", "antigravity"
    display_name: str
    auth_state: AuthState = AuthState.NOT_INSTALLED
    quota_source: QuotaSourceType = QuotaSourceType.UNAVAILABLE
    plan_name: Optional[str] = None
    billing_mode: Optional[str] = None  # "subscription", "api_billing", "hybrid", "unknown"

    # Local token usage by time window
    usage_five_hour: TokenUsageStats = field(default_factory=TokenUsageStats)
    usage_today: TokenUsageStats = field(default_factory=TokenUsageStats)
    usage_seven_day: TokenUsageStats = field(default_factory=TokenUsageStats)
    usage_thirty_day: TokenUsageStats = field(default_factory=TokenUsageStats)
    usage_all_time: TokenUsageStats = field(default_factory=TokenUsageStats)

    # Local token breakdown by model name (7-day window)
    model_usage_seven_day: Dict[str, TokenUsageStats] = field(default_factory=dict)
    # Local token breakdown by model name (All-time)
    model_usage_all_time: Dict[str, TokenUsageStats] = field(default_factory=dict)

    # Verified account-level or model-specific quota windows
    quota_windows: List[QuotaWindow] = field(default_factory=list)

    # Optional credits / extra usage info
    available_credits: Optional[float] = None
    credits_label: Optional[str] = None

    # Metadata & status indicators
    last_updated: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    last_activity_at: Optional[str] = None
    is_stale: bool = False
    stale_reason: Optional[str] = None
    status_message: str = ""
    scope_note: str = ""
    unsupported_notes: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    discovered_paths: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "display_name": self.display_name,
            "auth_state": self.auth_state.value,
            "quota_source": self.quota_source.value,
            "plan_name": self.plan_name,
            "billing_mode": self.billing_mode,
            "usage_five_hour": self.usage_five_hour.to_dict(),
            "usage_today": self.usage_today.to_dict(),
            "usage_seven_day": self.usage_seven_day.to_dict(),
            "usage_thirty_day": self.usage_thirty_day.to_dict(),
            "usage_all_time": self.usage_all_time.to_dict(),
            "model_usage_seven_day": {
                k: v.to_dict() for k, v in self.model_usage_seven_day.items()
            },
            "model_usage_all_time": {
                k: v.to_dict() for k, v in self.model_usage_all_time.items()
            },
            "quota_windows": [q.to_dict() for q in self.quota_windows],
            "available_credits": self.available_credits,
            "credits_label": self.credits_label,
            "last_updated": self.last_updated,
            "last_activity_at": self.last_activity_at,
            "is_stale": self.is_stale,
            "stale_reason": self.stale_reason,
            "status_message": self.status_message,
            "scope_note": self.scope_note,
            "unsupported_notes": list(self.unsupported_notes),
            "warnings": list(self.warnings),
            "discovered_paths": list(self.discovered_paths),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProviderSnapshot":
        return cls(
            provider_id=str(data.get("provider_id", "")),
            display_name=str(data.get("display_name", "")),
            auth_state=AuthState(data.get("auth_state", AuthState.NOT_INSTALLED.value)),
            quota_source=QuotaSourceType(
                data.get("quota_source", QuotaSourceType.UNAVAILABLE.value)
            ),
            plan_name=data.get("plan_name"),
            billing_mode=data.get("billing_mode"),
            usage_five_hour=TokenUsageStats.from_dict(data.get("usage_five_hour", {})),
            usage_today=TokenUsageStats.from_dict(data.get("usage_today", {})),
            usage_seven_day=TokenUsageStats.from_dict(data.get("usage_seven_day", {})),
            usage_thirty_day=TokenUsageStats.from_dict(data.get("usage_thirty_day", {})),
            usage_all_time=TokenUsageStats.from_dict(data.get("usage_all_time", {})),
            model_usage_seven_day={
                k: TokenUsageStats.from_dict(v)
                for k, v in (data.get("model_usage_seven_day") or {}).items()
            },
            model_usage_all_time={
                k: TokenUsageStats.from_dict(v)
                for k, v in (data.get("model_usage_all_time") or {}).items()
            },
            quota_windows=[
                QuotaWindow.from_dict(q) for q in (data.get("quota_windows") or [])
            ],
            available_credits=data.get("available_credits"),
            credits_label=data.get("credits_label"),
            last_updated=str(data.get("last_updated", "")),
            last_activity_at=data.get("last_activity_at"),
            is_stale=bool(data.get("is_stale", False)),
            stale_reason=data.get("stale_reason"),
            status_message=str(data.get("status_message", "")),
            scope_note=str(data.get("scope_note", "")),
            unsupported_notes=list(data.get("unsupported_notes") or []),
            warnings=list(data.get("warnings") or []),
            discovered_paths=list(data.get("discovered_paths") or []),
        )
