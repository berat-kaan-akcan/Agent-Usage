"""Command-line interface for Agent Usage Widget."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Dict, Optional

from . import __version__
from .core.models import ProviderSnapshot, TokenUsageStats
from .core.orchestrator import UsageOrchestrator
from .core.platform_info import detect_platform_environment, discover_provider_paths
from .core.settings import SettingsStore


def format_tokens(count: int) -> str:
    """Format token counts cleanly (e.g. 1.2M, 45.3K, 850)."""
    if count >= 1_000_000:
        return f"{count / 1_000_000:.2f}M"
    if count >= 1_000:
        return f"{count / 1_000:.1f}K"
    return str(count)


def render_text_report(snapshots: Dict[str, ProviderSnapshot], period: str = "seven_day") -> str:
    lines = [
        f"=== Agent Usage Report (v{__version__}) ===",
        f"Selected Time Period: {period}",
        "",
    ]
    for pid in ("claude", "codex", "antigravity"):
        snap = snapshots.get(pid)
        if not snap:
            continue
        lines.append(f"[{snap.display_name}]")
        lines.append(f"  Auth State   : {snap.auth_state.value.upper()}")
        lines.append(f"  Quota Source : {snap.quota_source.value}")
        if snap.plan_name:
            lines.append(f"  Plan / Tier  : {snap.plan_name} ({snap.billing_mode or 'standard'})")
        if snap.credits_label:
            lines.append(f"  Credits      : {snap.credits_label}")
        if snap.is_stale:
            lines.append(f"  Stale Notice : YES ({snap.stale_reason})")
        lines.append(f"  Status       : {snap.status_message}")

        stats: TokenUsageStats = getattr(snap, f"usage_{period}", snap.usage_seven_day)
        lines.append(
            f"  Local Tokens ({period}): "
            f"Total={format_tokens(stats.total_tokens)} "
            f"(In={format_tokens(stats.input_tokens)}, "
            f"CacheRead={format_tokens(stats.cache_read_tokens)}, "
            f"CacheCreate={format_tokens(stats.cache_creation_tokens)}, "
            f"Out={format_tokens(stats.output_tokens)}) "
            f"across {stats.session_count} session(s), {stats.message_count} step(s)"
        )

        if snap.quota_windows:
            lines.append("  Verified Account / Model Quotas:")
            for qw in snap.quota_windows:
                countdown = qw.time_until_reset_human() or "N/A"
                lines.append(
                    f"    - {qw.label:<28} : {qw.used_percent:5.1f}% used "
                    f"({qw.remaining_percent:5.1f}% rem) | Resets in {countdown} ({qw.resets_at or 'N/A'})"
                )
        else:
            lines.append("  Verified Account / Model Quotas: None available")

        if snap.model_usage_all_time:
            lines.append("  Recorded Local Usage by Model (All-Time):")
            for mname, mstats in snap.model_usage_all_time.items():
                lines.append(
                    f"    * {mname}: {format_tokens(mstats.total_tokens)} total "
                    f"(In={format_tokens(mstats.input_tokens)}, Out={format_tokens(mstats.output_tokens)})"
                )
        lines.append(f"  Scope Note   : {snap.scope_note}")
        lines.append("")
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agent-usage",
        description="Track Claude Code, OpenAI Codex, and Google Antigravity usage and verified quotas.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--json", action="store_true", help="Output full usage and quota report as JSON.")
    parser.add_argument("--status", action="store_true", help="Print human-readable usage and quota summary.")
    parser.add_argument("--doctor", action="store_true", help="Print platform, display server, and path diagnostics.")
    parser.add_argument("--no-network", action="store_true", help="Disable live network quota requests (local only).")
    parser.add_argument(
        "--period",
        choices=["five_hour", "today", "seven_day", "thirty_day", "all_time"],
        default="seven_day",
        help="Time window for local token aggregation.",
    )
    args = parser.parse_args(argv)

    if args.doctor:
        env = detect_platform_environment()
        paths = discover_provider_paths()
        diag = {
            "version": __version__,
            "platform": env.to_dict(),
            "discovered_paths": {
                "claude_dir": str(paths.claude_dir),
                "claude_dir_exists": paths.claude_dir.exists(),
                "codex_dir": str(paths.codex_dir),
                "codex_dir_exists": paths.codex_dir.exists(),
                "antigravity_brain_dir": str(paths.antigravity_brain_dir),
                "antigravity_brain_exists": paths.antigravity_brain_dir.exists(),
                "antigravity_state_dbs": [str(p) for p in paths.antigravity_state_dbs if p.exists()],
            },
        }
        print(json.dumps(diag, indent=2))
        return 0

    store = SettingsStore()
    settings = store.load()
    if args.no_network:
        settings.network_quota_enabled = False
    if args.period:
        settings.selected_time_period = args.period

    orchestrator = UsageOrchestrator()
    try:
        snapshots = orchestrator.refresh_sync(settings=settings)
    finally:
        orchestrator.shutdown()

    if args.json:
        print(
            json.dumps(
                {pid: snap.to_dict() for pid, snap in snapshots.items()},
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print(render_text_report(snapshots, period=settings.selected_time_period))
    return 0


if __name__ == "__main__":
    sys.exit(main())
