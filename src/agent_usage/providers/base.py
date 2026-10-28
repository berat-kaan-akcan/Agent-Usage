"""Base collector utilities, timestamp parsing, and credential redaction."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..core.models import ProviderSnapshot


_SECRET_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_-]{8,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}"),
    re.compile(r"ya29\.[A-Za-z0-9._-]{12,}"),
    re.compile(r"rt_[A-Za-z0-9._-]{10,}"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._-]+"),
]


def sanitize_message(text: str) -> str:
    """Ensure no credentials, tokens, or secrets ever leak into logs or UI messages."""
    if not text:
        return ""
    cleaned = str(text)
    for pattern in _SECRET_PATTERNS:
        if pattern.groups:
            cleaned = pattern.sub(r"\1[REDACTED]", cleaned)
        else:
            cleaned = pattern.sub("[REDACTED]", cleaned)
    return cleaned


def parse_iso_timestamp(value: Optional[object]) -> Optional[datetime]:
    """Parse an ISO-8601 string or epoch seconds/milliseconds into a UTC datetime."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        # Distinguish seconds vs milliseconds
        ts = float(value)
        if ts > 1e11:
            ts /= 1000.0
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            if raw.endswith("Z"):
                raw = raw[:-1] + "+00:00"
            dt = datetime.fromisoformat(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            try:
                num = float(raw)
                return parse_iso_timestamp(num)
            except ValueError:
                return None
    return None


class BaseProviderCollector(ABC):
    """Abstract base class for independent AI provider usage collectors."""

    provider_id: str
    display_name: str

    @abstractmethod
    def collect(
        self,
        allow_network: bool = True,
        now: Optional[datetime] = None,
        cached_snapshot: Optional[ProviderSnapshot] = None,
    ) -> ProviderSnapshot:
        """Collect local session usage and verified quota information."""
        raise NotImplementedError
