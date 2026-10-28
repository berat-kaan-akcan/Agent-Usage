# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller specification for Windows 10/11 and Linux standalone executables."""

import sys
from pathlib import Path

repo_root = Path(SPECPATH).resolve().parents[1]

a = Analysis(
    [str(repo_root / "src" / "agent_usage" / "main.py")],
    pathex=[str(repo_root / "src")],
    binaries=[],
    datas=[
        (str(repo_root / "packaging" / "linux" / "agent-usage-widget.svg"), "assets"),
        (str(repo_root / "LICENSE"), "."),
    ],
    hiddenimports=[
        "agent_usage",
        "agent_usage.cli",
        "agent_usage.core.models",
        "agent_usage.core.platform_info",
        "agent_usage.core.settings",
        "agent_usage.core.orchestrator",
        "agent_usage.providers.base",
        "agent_usage.providers.claude",
        "agent_usage.providers.codex",
        "agent_usage.providers.antigravity",
        "agent_usage.ui.theme",
        "agent_usage.ui.qt_widget",
        "agent_usage.ui.gtk_widget",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="agent-usage-widget",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False if sys.platform.startswith("win") else True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
