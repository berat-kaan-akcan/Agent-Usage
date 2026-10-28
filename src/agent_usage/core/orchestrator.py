"""Background refresh coordinator and multi-provider collection service."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

from ..providers.antigravity import AntigravityCollector
from ..providers.base import BaseProviderCollector, sanitize_message
from ..providers.claude import ClaudeCollector
from ..providers.codex import CodexCollector
from .models import AuthState, ProviderSnapshot
from .platform_info import ProviderPaths, discover_provider_paths
from .settings import SnapshotCache, WidgetSettings


class UsageOrchestrator:
    """Coordinates parallel, non-blocking collection and caching across all AI providers."""

    def __init__(
        self,
        paths: Optional[ProviderPaths] = None,
        cache: Optional[SnapshotCache] = None,
        collectors: Optional[Dict[str, BaseProviderCollector]] = None,
    ) -> None:
        self.paths = paths or discover_provider_paths()
        self.cache = cache or SnapshotCache()
        self.collectors: Dict[str, BaseProviderCollector] = collectors or {
            "claude": ClaudeCollector(self.paths),
            "codex": CodexCollector(self.paths),
            "antigravity": AntigravityCollector(self.paths),
        }
        self._executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="agent_usage_worker")
        self._latest_snapshots: Dict[str, ProviderSnapshot] = self.cache.load_all()

    def get_cached_snapshots(self) -> Dict[str, ProviderSnapshot]:
        return dict(self._latest_snapshots)

    def refresh_sync(
        self,
        settings: Optional[WidgetSettings] = None,
        now: Optional[datetime] = None,
    ) -> Dict[str, ProviderSnapshot]:
        """Collect all enabled providers in parallel using worker threads and persist cache."""
        cfg = settings or WidgetSettings()
        allow_network = cfg.network_quota_enabled
        enabled = set(cfg.enabled_providers or ["claude", "codex", "antigravity"])
        current_time = now or datetime.now(timezone.utc)

        futures = {}
        for pid, collector in self.collectors.items():
            if pid not in enabled:
                continue
            cached_snap = self._latest_snapshots.get(pid)
            futures[pid] = self._executor.submit(
                self._safe_collect, collector, allow_network, current_time, cached_snap
            )

        results: Dict[str, ProviderSnapshot] = {}
        for pid, fut in futures.items():
            results[pid] = fut.result(timeout=15.0)

        self._latest_snapshots.update(results)
        try:
            self.cache.save_all(self._latest_snapshots)
        except Exception:
            pass
        return results

    def refresh_async(
        self,
        callback: Callable[[Dict[str, ProviderSnapshot]], None],
        settings: Optional[WidgetSettings] = None,
    ) -> None:
        """Run refresh in a background thread and invoke callback upon completion."""

        def _task() -> None:
            snaps = self.refresh_sync(settings=settings)
            callback(snaps)

        self._executor.submit(_task)

    @staticmethod
    def _safe_collect(
        collector: BaseProviderCollector,
        allow_network: bool,
        now: datetime,
        cached_snap: Optional[ProviderSnapshot],
    ) -> ProviderSnapshot:
        try:
            return collector.collect(
                allow_network=allow_network,
                now=now,
                cached_snapshot=cached_snap,
            )
        except Exception as exc:
            return ProviderSnapshot(
                provider_id=collector.provider_id,
                display_name=collector.display_name,
                auth_state=AuthState.ERROR,
                status_message=sanitize_message(f"Collector error: {exc}"),
                last_updated=now.isoformat(),
            )

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False)
