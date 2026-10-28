"""Main entrypoint for the Agent Usage Desktop Widget."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

from . import __version__
from .cli import main as cli_main
from .ui.gtk_widget import is_gtk_available, run_gtk_widget
from .ui.qt_widget import is_qt_available, run_qt_widget


def find_native_qt_binary() -> Optional[Path]:
    """Locate compiled C++ Qt6 native binary if built."""
    repo_root = Path(__file__).resolve().parents[2]
    candidates = [
        repo_root / "build" / "agent-usage-qt",
        repo_root / "build" / "agent-usage-qt.exe",
        Path("/usr/bin/agent-usage-qt"),
        Path("/usr/local/bin/agent-usage-qt"),
    ]
    for c in candidates:
        if c.is_file() and os.access(c, os.X_OK):
            return c
    which_bin = shutil.which("agent-usage-qt")
    return Path(which_bin) if which_bin else None


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agent-usage-widget",
        description="Cross-Platform Desktop Widget for Claude Code, OpenAI Codex, and Google Antigravity.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--backend",
        choices=["auto", "qt", "gtk", "native-qt", "cli"],
        default="auto",
        help="Preferred UI toolkit backend.",
    )
    parser.add_argument(
        "--minimized",
        action="store_true",
        help="Start minimized to system tray.",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Initialize UI, perform one refresh cycle, and exit cleanly (for automated tests).",
    )
    args, remaining = parser.parse_known_args(argv)

    if args.backend == "cli":
        return cli_main(remaining)

    if args.backend in ("auto", "qt") and is_qt_available():
        return run_qt_widget(start_minimized=args.minimized, self_test=args.self_test)

    if args.backend == "native-qt":
        native_bin = find_native_qt_binary()
        if native_bin:
            cmd = [str(native_bin)]
            if args.minimized:
                cmd.append("--minimized")
            if args.self_test:
                cmd.append("--self-test")
            return subprocess.call(cmd)

    if args.backend in ("auto", "gtk") and is_gtk_available():
        if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
            return run_gtk_widget(start_minimized=args.minimized, self_test=args.self_test)

    native_bin = find_native_qt_binary()
    if native_bin:
        cmd = [str(native_bin)]
        if args.minimized:
            cmd.append("--minimized")
        if args.self_test:
            cmd.append("--self-test")
        return subprocess.call(cmd)

    # Headless fallback to CLI status report
    return cli_main(["--status"])


if __name__ == "__main__":
    sys.exit(main())
