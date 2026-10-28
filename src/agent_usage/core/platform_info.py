"""Platform-aware environment detection, path discovery, and autostart configuration."""

from __future__ import annotations

import os
import platform
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class PlatformEnvironment:
    """Detected operating system, desktop environment, display server, and windowing capabilities."""

    os_name: str  # "linux", "windows", "darwin"
    os_release: str
    distro_id: str
    desktop_env: str  # "KDE", "GNOME", "Windows", "Other"
    session_type: str  # "wayland", "x11", "windows", "macos", "headless"
    always_on_top_supported: bool
    always_on_top_note: str
    window_positioning_note: str
    tray_supported: bool

    def to_dict(self) -> Dict[str, object]:
        return {
            "os_name": self.os_name,
            "os_release": self.os_release,
            "distro_id": self.distro_id,
            "desktop_env": self.desktop_env,
            "session_type": self.session_type,
            "always_on_top_supported": self.always_on_top_supported,
            "always_on_top_note": self.always_on_top_note,
            "window_positioning_note": self.window_positioning_note,
            "tray_supported": self.tray_supported,
        }


def detect_platform_environment() -> PlatformEnvironment:
    """Detect current OS, Linux distro, desktop environment, and X11/Wayland capabilities."""
    sys_name = platform.system().lower()
    os_release = platform.release()
    distro_id = sys_name

    if sys_name == "windows":
        return PlatformEnvironment(
            os_name="windows",
            os_release=os_release,
            distro_id="windows",
            desktop_env="Windows Explorer",
            session_type="windows",
            always_on_top_supported=True,
            always_on_top_note="Supported natively via Win32 HWND_TOPMOST / Qt::WindowStaysOnTopHint.",
            window_positioning_note="Full programmatic window dragging and screen coordinates supported.",
            tray_supported=True,
        )

    if sys_name == "darwin":
        return PlatformEnvironment(
            os_name="darwin",
            os_release=os_release,
            distro_id="macos",
            desktop_env="Aqua",
            session_type="macos",
            always_on_top_supported=True,
            always_on_top_note="Supported via NSWindow.level floating window.",
            window_positioning_note="Full programmatic window positioning supported.",
            tray_supported=True,
        )

    # Linux detection
    os_release_file = Path("/etc/os-release")
    if os_release_file.is_file():
        try:
            for line in os_release_file.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.startswith("ID="):
                    distro_id = line.split("=", 1)[1].strip().strip('"')
                    break
        except Exception:
            pass

    xdg_desktop = os.environ.get("XDG_CURRENT_DESKTOP", "") or os.environ.get("DESKTOP_SESSION", "")
    desktop_upper = xdg_desktop.upper()
    if "KDE" in desktop_upper or "PLASMA" in desktop_upper:
        desktop_env = "KDE Plasma"
    elif "GNOME" in desktop_upper or "UBUNTU" in desktop_upper:
        desktop_env = "GNOME"
    elif xdg_desktop:
        desktop_env = xdg_desktop
    else:
        desktop_env = "Linux Desktop"

    session_type = os.environ.get("XDG_SESSION_TYPE", "").lower()
    if not session_type:
        if os.environ.get("WAYLAND_DISPLAY"):
            session_type = "wayland"
        elif os.environ.get("DISPLAY"):
            session_type = "x11"
        else:
            session_type = "headless"

    if session_type == "wayland":
        always_on_top_supported = False
        always_on_top_note = (
            f"Running on {desktop_env} (Wayland). Standard Wayland xdg-shell restricts "
            "unprivileged clients from forcing global Always-On-Top or arbitrary screen coordinates. "
            "Always-on-top is requested via window hints and works under XWayland or via KDE KWin / "
            "GNOME window menu ('Keep Above Others')."
        )
        window_positioning_note = (
            "Interactive dragging via compositor surface move (beginSystemMove) works natively on Wayland; "
            "absolute global x/y restore requires X11 or compositor window rules."
        )
    elif session_type == "x11":
        always_on_top_supported = True
        always_on_top_note = f"Supported natively on {desktop_env} (X11 _NET_WM_STATE_ABOVE)."
        window_positioning_note = "Full X11 coordinate persistence and frameless dragging supported."
    else:
        always_on_top_supported = False
        always_on_top_note = "Headless session detected."
        window_positioning_note = "Headless session detected."

    return PlatformEnvironment(
        os_name="linux",
        os_release=os_release,
        distro_id=distro_id,
        desktop_env=desktop_env,
        session_type=session_type,
        always_on_top_supported=always_on_top_supported,
        always_on_top_note=always_on_top_note,
        window_positioning_note=window_positioning_note,
        tray_supported=True,
    )


def get_app_config_dir() -> Path:
    """Return cross-platform configuration directory for Agent Usage Widget."""
    override = os.environ.get("AGENT_USAGE_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    if platform.system().lower() == "windows":
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else (Path.home() / "AppData" / "Roaming")
        return base / "AgentUsageWidget"
    xdg_config = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg_config) if xdg_config else (Path.home() / ".config")
    return base / "agent-usage-widget"


def get_app_cache_dir() -> Path:
    """Return cross-platform cache directory for Agent Usage Widget."""
    override = os.environ.get("AGENT_USAGE_CACHE_DIR")
    if override:
        return Path(override).expanduser()
    if platform.system().lower() == "windows":
        localappdata = os.environ.get("LOCALAPPDATA")
        base = Path(localappdata) if localappdata else (Path.home() / "AppData" / "Local")
        return base / "AgentUsageWidget" / "Cache"
    xdg_cache = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg_cache) if xdg_cache else (Path.home() / ".cache")
    return base / "agent-usage-widget"


@dataclass
class ProviderPaths:
    """Discovered cross-platform paths for Claude Code, OpenAI Codex, and Google Antigravity."""

    claude_dir: Path
    claude_credentials_file: Path
    claude_projects_dir: Path
    claude_stats_cache_file: Path

    codex_dir: Path
    codex_auth_file: Path
    codex_sessions_dir: Path

    antigravity_gemini_dir: Path
    antigravity_brain_dir: Path
    antigravity_config_dirs: List[Path] = field(default_factory=list)
    antigravity_state_dbs: List[Path] = field(default_factory=list)
    antigravity_log_files: List[Path] = field(default_factory=list)


def discover_provider_paths(home_dir: Optional[Path] = None) -> ProviderPaths:
    """Discover provider directories across Linux, Windows 10/11, and macOS."""
    home = home_dir or Path.home()
    sys_name = platform.system().lower()

    # 1. Claude Code paths
    claude_env = os.environ.get("CLAUDE_CONFIG_DIR")
    claude_dir = Path(claude_env).expanduser() if claude_env else (home / ".claude")
    claude_credentials_file = claude_dir / ".credentials.json"
    claude_projects_dir = claude_dir / "projects"
    claude_stats_cache_file = claude_dir / "stats-cache.json"

    # 2. OpenAI Codex paths
    codex_env = os.environ.get("CODEX_HOME")
    codex_dir = Path(codex_env).expanduser() if codex_env else (home / ".codex")
    codex_auth_file = codex_dir / "auth.json"
    codex_sessions_dir = codex_dir / "sessions"

    # 3. Google Antigravity paths
    agy_env = os.environ.get("ANTIGRAVITY_HOME")
    antigravity_gemini_dir = (
        Path(agy_env).expanduser() if agy_env else (home / ".gemini" / "antigravity")
    )
    antigravity_brain_dir = antigravity_gemini_dir / "brain"

    config_roots: List[Path] = []
    if sys_name == "windows" and home_dir is None:
        appdata = os.environ.get("APPDATA")
        if appdata:
            config_roots.append(Path(appdata))
        config_roots.append(home / "AppData" / "Roaming")
    else:
        if home_dir is None:
            xdg = os.environ.get("XDG_CONFIG_HOME")
            if xdg:
                config_roots.append(Path(xdg))
        config_roots.append(home / ".config")
        config_roots.append(home / "AppData" / "Roaming")

    antigravity_config_dirs: List[Path] = []
    antigravity_state_dbs: List[Path] = []
    antigravity_log_files: List[Path] = []

    seen_dirs = set()
    for root in config_roots:
        for folder_name in ("Antigravity", "Antigravity IDE"):
            candidate = root / folder_name
            if str(candidate) in seen_dirs:
                continue
            seen_dirs.add(str(candidate))
            antigravity_config_dirs.append(candidate)
            db_candidate = candidate / "User" / "globalStorage" / "state.vscdb"
            antigravity_state_dbs.append(db_candidate)
            log_candidate = candidate / "logs" / "language_server.log"
            antigravity_log_files.append(log_candidate)

    return ProviderPaths(
        claude_dir=claude_dir,
        claude_credentials_file=claude_credentials_file,
        claude_projects_dir=claude_projects_dir,
        claude_stats_cache_file=claude_stats_cache_file,
        codex_dir=codex_dir,
        codex_auth_file=codex_auth_file,
        codex_sessions_dir=codex_sessions_dir,
        antigravity_gemini_dir=antigravity_gemini_dir,
        antigravity_brain_dir=antigravity_brain_dir,
        antigravity_config_dirs=antigravity_config_dirs,
        antigravity_state_dbs=antigravity_state_dbs,
        antigravity_log_files=antigravity_log_files,
    )


class AutostartManager:
    """Cross-platform login autostart manager (Linux XDG `.desktop` and Windows Startup / Registry)."""

    APP_ID = "agent-usage-widget"

    @classmethod
    def get_linux_autostart_file(cls, config_home: Optional[Path] = None) -> Path:
        if config_home is not None:
            base = config_home
        else:
            xdg = os.environ.get("XDG_CONFIG_HOME")
            base = Path(xdg) if xdg else (Path.home() / ".config")
        return base / "autostart" / f"{cls.APP_ID}.desktop"

    @classmethod
    def get_windows_startup_vbs(cls, appdata_dir: Optional[Path] = None) -> Path:
        if appdata_dir is not None:
            base = appdata_dir
        else:
            appdata = os.environ.get("APPDATA")
            base = Path(appdata) if appdata else (Path.home() / "AppData" / "Roaming")
        return (
            base
            / "Microsoft"
            / "Windows"
            / "Start Menu"
            / "Programs"
            / "Startup"
            / f"{cls.APP_ID}.vbs"
        )

    @classmethod
    def is_enabled(cls, config_home: Optional[Path] = None) -> bool:
        if platform.system().lower() == "windows" and config_home is None:
            return cls.get_windows_startup_vbs().is_file()
        return cls.get_linux_autostart_file(config_home).is_file()

    @classmethod
    def set_enabled(
        cls,
        enabled: bool,
        exec_command: Optional[str] = None,
        config_home: Optional[Path] = None,
    ) -> bool:
        """Create or remove the login autostart entry."""
        if not exec_command:
            exec_command = f'"{sys.executable}" -m agent_usage.main --minimized'

        if platform.system().lower() == "windows" and config_home is None:
            target = cls.get_windows_startup_vbs()
            if not enabled:
                if target.is_file():
                    target.unlink()
                return False
            target.parent.mkdir(parents=True, exist_ok=True)
            escaped = exec_command.replace('"', '""')
            vbs_content = (
                'Set WshShell = CreateObject("WScript.Shell")\n'
                f'WshShell.Run "{escaped}", 0, False\n'
            )
            target.write_text(vbs_content, encoding="utf-8")
            return True

        desktop_file = cls.get_linux_autostart_file(config_home)
        if not enabled:
            if desktop_file.is_file():
                desktop_file.unlink()
            return False

        desktop_file.parent.mkdir(parents=True, exist_ok=True)
        content = (
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Version=1.0\n"
            "Name=Agent Usage Widget\n"
            "Comment=Track Claude Code, OpenAI Codex, and Google Antigravity usage\n"
            f"Exec={exec_command}\n"
            "Icon=utilities-system-monitor\n"
            "Terminal=false\n"
            "Categories=Utility;Development;Monitor;\n"
            "StartupNotify=false\n"
            "X-GNOME-Autostart-enabled=true\n"
        )
        desktop_file.write_text(content, encoding="utf-8")
        return True
