"""Tests for UsageOrchestrator, CLI output, and Native Qt6 widget integration."""

from __future__ import annotations

import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from agent_usage.cli import format_tokens, main as cli_main
from agent_usage.core.orchestrator import UsageOrchestrator
from agent_usage.core.platform_info import discover_provider_paths
from agent_usage.core.settings import SnapshotCache, WidgetSettings
from agent_usage.main import find_native_qt_binary


class TestOrchestratorAndIntegration(unittest.TestCase):
    def test_format_tokens(self) -> None:
        self.assertEqual(format_tokens(500), "500")
        self.assertEqual(format_tokens(1500), "1.5K")
        self.assertEqual(format_tokens(2_500_000), "2.50M")

    def test_orchestrator_parallel_sync_and_async(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = discover_provider_paths(home_dir=Path(tmpdir))
            paths.claude_dir = Path(tmpdir) / ".claude"
            paths.codex_dir = Path(tmpdir) / ".codex"
            paths.antigravity_gemini_dir = Path(tmpdir) / ".gemini" / "antigravity"
            paths.antigravity_brain_dir = paths.antigravity_gemini_dir / "brain"
            paths.antigravity_state_dbs = []
            paths.antigravity_log_files = []

            cache = SnapshotCache(Path(tmpdir) / "cache.json")
            orch = UsageOrchestrator(paths=paths, cache=cache)
            try:
                snaps = orch.refresh_sync(settings=WidgetSettings(network_quota_enabled=False))
                self.assertEqual(set(snaps.keys()), {"claude", "codex", "antigravity"})
                self.assertTrue((Path(tmpdir) / "cache.json").is_file())
            finally:
                orch.shutdown()

    def test_cli_json_and_doctor_commands(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli_main(["--json", "--no-network"])
        self.assertEqual(rc, 0)
        parsed = json.loads(buf.getvalue())
        self.assertIn("claude", parsed)
        self.assertIn("codex", parsed)
        self.assertIn("antigravity", parsed)

        buf_doc = io.StringIO()
        with redirect_stdout(buf_doc):
            rc_doc = cli_main(["--doctor"])
        self.assertEqual(rc_doc, 0)
        doc_json = json.loads(buf_doc.getvalue())
        self.assertIn("platform", doc_json)
        self.assertIn("discovered_paths", doc_json)

    def test_native_qt_binary_self_test_if_built(self) -> None:
        native_bin = find_native_qt_binary()
        if native_bin is not None:
            proc = subprocess.run(
                [str(native_bin), "--self-test"],
                capture_output=True,
                text=True,
                timeout=10.0,
            )
            self.assertEqual(proc.returncode, 0)
            self.assertIn("Native Qt6 Agent Usage Widget self-test completed!", proc.stdout)


if __name__ == "__main__":
    unittest.main()
