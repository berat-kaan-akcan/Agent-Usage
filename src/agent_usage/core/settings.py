"""Persistent widget configuration and local snapshot cache."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import ProviderSnapshot
from .platform_info import get_app_cache_dir, get_app_config_dir


@dataclass
class WidgetSettings:
    """User-configurable settings for the Agent Usage Desktop Widget."""

    view_mode: str = "expanded"  # "compact" or "expanded"
    theme: str = "dark"  # "dark" or "light"
    always_on_top: bool = True
    opacity: float = 0.95  # 0.30 to 1.00
    refresh_interval_seconds: int = 60  # 15 to 3600
    minimize_to_tray: bool = True
    start_minimized: bool = False
    start_with_system: bool = False
    network_quota_enabled: bool = True
    window_x: int = 80
    window_y: int = 80
    window_width: int = 400
    window_height: int = 620
    enabled_providers: List[str] = field(
        default_factory=lambda: ["claude", "codex", "antigravity"]
    )
    selected_time_period: str = "seven_day"  # "five_hour", "today", "seven_day", "thirty_day", "all_time"

    def validate(self) -> None:
        """Clamp and normalize settings values."""
        if self.view_mode not in ("compact", "expanded"):
            self.view_mode = "expanded"
        if self.theme not in ("dark", "light"):
            self.theme = "dark"
        try:
            self.opacity = max(0.35, min(1.0, float(self.opacity)))
        except (TypeError, ValueError):
            self.opacity = 0.95
        try:
            self.refresh_interval_seconds = max(
                15, min(3600, int(self.refresh_interval_seconds))
            )
        except (TypeError, ValueError):
            self.refresh_interval_seconds = 60
        try:
            self.window_width = max(280, min(1600, int(self.window_width)))
            self.window_height = max(180, min(1600, int(self.window_height)))
        except (TypeError, ValueError):
            self.window_width = 400
            self.window_height = 620
        valid_periods = ("five_hour", "today", "seven_day", "thirty_day", "all_time")
        if self.selected_time_period not in valid_periods:
            self.selected_time_period = "seven_day"

    def to_dict(self) -> Dict[str, Any]:
        self.validate()
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WidgetSettings":
        settings = cls(
            view_mode=str(data.get("view_mode", "expanded")),
            theme=str(data.get("theme", "dark")),
            always_on_top=bool(data.get("always_on_top", True)),
            opacity=float(data.get("opacity", 0.95)),
            refresh_interval_seconds=int(data.get("refresh_interval_seconds", 60)),
            minimize_to_tray=bool(data.get("minimize_to_tray", True)),
            start_minimized=bool(data.get("start_minimized", False)),
            start_with_system=bool(data.get("start_with_system", False)),
            network_quota_enabled=bool(data.get("network_quota_enabled", True)),
            window_x=int(data.get("window_x", 80)),
            window_y=int(data.get("window_y", 80)),
            window_width=int(data.get("window_width", 400)),
            window_height=int(data.get("window_height", 620)),
            enabled_providers=list(
                data.get("enabled_providers") or ["claude", "codex", "antigravity"]
            ),
            selected_time_period=str(data.get("selected_time_period", "seven_day")),
        )
        settings.validate()
        return settings


class SettingsStore:
    """Thread-safe atomic JSON persistence for WidgetSettings."""

    def __init__(self, settings_path: Optional[Path] = None) -> None:
        self.settings_path = settings_path or (get_app_config_dir() / "settings.json")

    def load(self) -> WidgetSettings:
        if not self.settings_path.is_file():
            return WidgetSettings()
        try:
            raw = json.loads(self.settings_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return WidgetSettings.from_dict(raw)
        except Exception:
            pass
        return WidgetSettings()

    def save(self, settings: WidgetSettings) -> None:
        settings.validate()
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(settings.to_dict(), indent=2, sort_keys=True)
        fd, tmp_path = tempfile.mkstemp(
            dir=str(self.settings_path.parent), prefix=".settings_", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
            os.replace(tmp_path, self.settings_path)
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass


class SnapshotCache:
    """Atomic local cache of provider snapshots for stale-data fallback when offline or rate-limited."""

    def __init__(self, cache_path: Optional[Path] = None) -> None:
        self.cache_path = cache_path or (get_app_cache_dir() / "snapshots_cache.json")

    def load_all(self) -> Dict[str, ProviderSnapshot]:
        if not self.cache_path.is_file():
            return {}
        try:
            raw = json.loads(self.cache_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return {}
            result: Dict[str, ProviderSnapshot] = {}
            for pid, snap_dict in raw.items():
                if isinstance(snap_dict, dict):
                    result[pid] = ProviderSnapshot.from_dict(snap_dict)
            return result
        except Exception:
            return {}

    def save_all(self, snapshots: Dict[str, ProviderSnapshot]) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {pid: snap.to_dict() for pid, snap in snapshots.items()},
            indent=2,
            sort_keys=True,
        )
        fd, tmp_path = tempfile.mkstemp(
            dir=str(self.cache_path.parent), prefix=".cache_", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
            os.replace(tmp_path, self.cache_path)
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
