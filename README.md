# Agent Usage Widget

A cross-platform desktop widget and CLI that tracks **Claude Code**, **OpenAI Codex**, and **Google Antigravity** usage and verified account/model quotas in one place.

![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)
![Platforms](https://img.shields.io/badge/Platforms-Windows%2010%2F11%20%7C%20Ubuntu%20%7C%20Arch%2FCachyOS-success)
![Desktops](https://img.shields.io/badge/Desktop-KDE%20Plasma%20%7C%20GNOME%20%7C%20X11%20%7C%20Wayland-informational)

---

## 1. Overview & Key Features

### Desktop Widget Features
- **Compact & Expanded Views:** Switch instantly between a minimal 2-bar summary and a detailed model-by-model view.
- **Always-on-Top Option:** Uses `Qt::WindowStaysOnTopHint` / `set_keep_above()` where supported by the OS and window manager.
- **Draggable & Resizable Window:** Supports interactive surface moving (`startSystemMove()` on Wayland/X11/Windows) and persistent geometry.
- **Light & Dark Themes:** Clean, high-contrast dark (`#0F141C`) and light (`#F8FAFC`) themes.
- **Adjustable Opacity:** Real-time window opacity slider (`40%`–`100%`).
- **System Tray Integration & Minimize to Tray:** Tray icon with context menu (`Show/Hide`, `Refresh Now`, `Toggle Compact/Expanded`, `Quit`).
- **Start with System Login:** Creates standard XDG autostart `.desktop` entries on Linux (`~/.config/autostart/agent-usage-widget.desktop`) and Startup launcher scripts on Windows (`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup`).
- **Configurable Refresh & Caching:** Automatic background refresh (`15s`–`3600s`), manual refresh button, last-updated timestamps, and atomic local cache (`snapshots_cache.json`) for offline/rate-limit resilience.
- **Clear Status Badges:** Distinguishes `AUTHENTICATED`, `LOCAL_ONLY`, `EXPIRED`, `RATE_LIMITED`, `UNAUTHENTICATED`, `NOT_INSTALLED`, and `STALE` states.

---

## 2. Provider Data Sources & Verification Scope

For every provider, **Agent Usage Widget** strictly separates **recorded local token activity** from **verified account-level or model-level quotas**. It never fabricates percentages, endpoints, or reset times, and never infers remaining quota from local token counts.

### A. Claude Code (`src/agent_usage/providers/claude.py`)
- **Local Session History:** Reads `~/.claude/projects/<project-slug>/<session>.jsonl` (`%USERPROFILE%\.claude\projects\...` on Windows, overrideable via `CLAUDE_CONFIG_DIR`).
  - Deduplicates streaming `type == "assistant"` chunks by `message.id` (keeping the final token tally per turn).
  - Aggregates `input_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`, and `output_tokens` across **5-Hour**, **Today**, **7-Day**, **30-Day**, and **All-Time** periods, broken down by model (`claude-sonnet-4-5`, `claude-opus-4-1`, etc.).
- **Subscription Quotas:** Reads `~/.claude/.credentials.json` (`claudeAiOauth.accessToken`, `subscriptionType`, `rateLimitTier`, `expiresAt`) and queries `GET https://api.anthropic.com/api/oauth/usage` with `anthropic-beta: oauth-2025-04-20` and `User-Agent: claude-code/1.0.35`.
  - Parses verified `five_hour`, `seven_day`, `seven_day_sonnet`, `seven_day_opus` utilization percentages and UTC `resets_at` timestamps, plus `extra_usage` credits.
- **Shared Subscription Scope:** Anthropic's 5-hour and 7-day subscription limits are shared across **Claude Code**, **Claude Web (`claude.ai`)**, and **Claude Desktop**. However, individual Claude Web and Claude Desktop conversation token counts are not stored locally and are explicitly marked as unsupported rather than estimated.

### B. OpenAI Codex (`src/agent_usage/providers/codex.py`)
- **Local Session History:** Reads `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` (`%USERPROFILE%\.codex\sessions\...` on Windows, overrideable via `CODEX_HOME`).
  - Computes exact per-turn token deltas from cumulative `total_token_usage` (and `last_token_usage` fallback), deduplicating repeated cumulative snapshots and separating uncached `input_tokens`, `cached_input_tokens`, `output_tokens`, and `reasoning_output_tokens`.
- **Account-Level Quotas (Dual Verification):**
  1. **Embedded Session Rate-Limit Snapshots (Zero-Network):** Extracts the server-reported `rate_limits` object (`plan_type`, `primary` 5-hour window, `secondary` 7-day window, `credits`) recorded directly inside Codex `token_count` events.
  2. **Live Backend Quota Endpoint:** Reads `~/.codex/auth.json` (`tokens.access_token`, `tokens.account_id`) and queries `GET https://chatgpt.com/backend-api/wham/usage`.
- **Subscription vs. API Billing:** Explicitly inspects `auth_mode` and `OPENAI_API_KEY` in `~/.codex/auth.json`. When configured with an OpenAI Platform API key (`auth_mode == "api_key"`), the widget labels the provider as `OpenAI Platform API (Pay-per-token)` (`quota_source: api_billing`) instead of fabricating 5-hour/7-day subscription bars.

### C. Google Antigravity (`src/agent_usage/providers/antigravity.py`)
- **Local Conversation History:** Reads `~/.gemini/antigravity/brain/<conversation-id>/.system_generated/logs/transcript.jsonl` (`%USERPROFILE%\.gemini\antigravity\brain\...` on Windows).
  - Parses exact `input_tokens`, `cache_read_tokens`, and `output_tokens` from `source == "MODEL"` steps, deduplicating by `step_index` and tracking active model selection changes.
- **Model-Specific Quotas & Reset Times (Verified):**
  1. **Local `language_server` Connect-RPC:** Discovers the active localhost HTTP port from `~/.config/Antigravity/logs/language_server.log` (`%APPDATA%\Antigravity\logs\language_server.log` on Windows) and queries `POST http://127.0.0.1:<port>/exa.language_server_pb.LanguageServerService/GetUserStatus`.
  2. **Read-Only SQLite + Protobuf State Inspection (`state.vscdb`):** Opens `~/.config/Antigravity IDE/User/globalStorage/state.vscdb` in strict read-only URI mode (`?mode=ro`) and decodes the nested Protobuf wire format in `antigravityUnifiedStateSync.userStatus` and `antigravityUnifiedStateSync.modelCredits`. Extracts the subscription tier (e.g., `Google AI Pro`), available model credits, and exact `remainingFraction` + UTC `resetTime` for every model in the pool (`Gemini 3.8 Flash`, `Claude Opus 5.5`, `Claude Sonnet 5.5`, `GPT-OSS 120B`).
- **Strict Non-Inference Rule:** Never infers remaining quota from local transcript token history. Also documents that chat UI slash commands (such as `/compact` or `/goal`) are interactive assistant shortcuts rather than machine-readable quota APIs.

---

## 3. Architecture & Supported Frontends

The project provides a unified cross-platform architecture in one repository:

1. **Native Qt6 C++17 Desktop Widget (`native-qt/` → `build/agent-usage-qt`)**
   - Compiled native binary using `Qt6::Widgets`, `Qt6::Gui`, and `Qt6::Core`.
   - Zero Python GUI package dependencies required at runtime; invokes the `agent_usage` collector asynchronously via `QProcess`.
2. **Python Multi-Backend Desktop Widget (`src/agent_usage/ui/`)**
   - **Qt6 Backend (`qt_widget.py`):** Uses `PySide6` or `PyQt6` on Windows 10/11, Ubuntu, and Arch/CachyOS.
   - **GTK3 + AppIndicator Backend (`gtk_widget.py`):** Uses `PyGObject` (`Gtk 3.0` + `AyatanaAppIndicator3`) natively on Linux desktops.
3. **CLI & Diagnostic Tool (`src/agent_usage/cli.py`)**
   - `agent-usage --status`: Formatted terminal report.
   - `agent-usage --json`: Machine-readable JSON output for scripts/bars.
   - `agent-usage --doctor`: Platform, display server, and path diagnostics.

---

## 4. Platform Compatibility & Honest Limitations (Windows, X11, Wayland)

| Environment | Tested Status | Window Positioning & Dragging | Always-on-Top (`Keep Above`) | System Tray |
| :--- | :--- | :--- | :--- | :--- |
| **Arch / CachyOS (KDE Plasma Wayland)** | **Verified & Tested on Live System** | Dragging works natively via `QWindow::startSystemMove()`. Global `x, y` coordinate restoration is restricted by the Wayland `xdg-shell` protocol unless a KWin Window Rule is set. | Requested via `WindowStaysOnTopHint`. Because standard Wayland prevents unprivileged clients from forcing global top-most stacking, users can also right-click the titlebar → *More Actions* → *Keep Above Others* (or add a KWin Window Rule). | Supported via `QSystemTrayIcon` (StatusNotifierItem) & `AyatanaAppIndicator3`. |
| **Linux X11 (KDE Plasma / GNOME)** | **Supported (Verified via offscreen/X11 hints)** | Full programmatic `x, y` position save/restore and frameless dragging. | Supported natively via `_NET_WM_STATE_ABOVE`. | Supported via XEmbed / StatusNotifierItem. *(Note: Vanilla GNOME requires the Ubuntu AppIndicator / KStatusNotifierItem extension for tray icons).* |
| **Windows 10 & Windows 11** | **Supported (Build & Installer configs provided)** | Full Win32 screen coordinate persistence and window dragging. | Supported natively via Win32 `HWND_TOPMOST`. | Supported natively in Windows Taskbar Notification Area. |

---

## 5. Quick Start, Build & Installation

### A. Run Directly from Source (Linux / Windows)

```bash
# 1. Run the CLI usage & quota report
PYTHONPATH=src python3 -m agent_usage.cli --status

# 2. Run platform & path diagnostics
PYTHONPATH=src python3 -m agent_usage.cli --doctor

# 3. Launch the Desktop Widget (auto-selects Qt, GTK, or Native Qt binary)
PYTHONPATH=src python3 -m agent_usage.main
```

### B. Build & Run the Native Qt6 C++ Desktop Widget (Linux / Windows with Qt6)

```bash
cmake -B build -S native-qt -GNinja -DCMAKE_BUILD_TYPE=Release
cmake --build build

# Run the native desktop widget
./build/agent-usage-qt

# Run automated headless self-test
QT_QPA_PLATFORM=offscreen ./build/agent-usage-qt --self-test
```

### C. Arch Linux / CachyOS Package (`PKGBUILD`)

Tested and verified on CachyOS Linux:

```bash
cd packaging/arch
makepkg -si
```
Pre-built artifact generated during verification:
- `dist/agent-usage-widget-1.0.0-1-x86_64.pkg.tar.zst`

### D. Ubuntu / Debian Package (`.deb`)

Can be built on any machine (even without `dpkg-deb` installed) using the included zero-dependency builder:

```bash
python3 packaging/debian/build_deb.py
sudo apt install ./dist/agent-usage-widget_1.0.0-1_amd64.deb
```
Pre-built artifact generated during verification:
- `dist/agent-usage-widget_1.0.0-1_amd64.deb`

### E. Linux AppDir / AppImage

```bash
./packaging/appimage/build_appimage.sh
# Run directly from the portable AppDir:
./dist/AgentUsageWidget.AppDir/AppRun
```
*(If `appimagetool` is installed in `PATH`, `build_appimage.sh` also outputs `dist/AgentUsageWidget-x86_64.AppImage`).*

### F. Windows 10 & Windows 11 Executable & Installer

On a Windows 10/11 machine with Python 3.10+:

```powershell
.\packaging\windows\build_windows.ps1
```
This script runs the full unit test suite, installs `PySide6` and `PyInstaller` into an isolated `.venv`, builds `dist\agent-usage-widget.exe` via `packaging\windows\agent-usage-widget.spec`, and compiles the Windows Setup installer (`dist\AgentUsageWidget-Setup-1.0.0-win64.exe`) if Inno Setup (`iscc`) is installed.

---

## 6. Running Automated Tests

The test suite (`tests/`) includes 26 unit, fixture, and stress tests covering local JSONL parsing, streaming deduplication, cumulative delta calculations, malformed JSONL lines, negative token rejection, expired OAuth credentials, HTTP 401/429/timeout fallbacks, SQLite `state.vscdb` Protobuf decoding, truncated Protobuf resilience, settings clamping, and credential redaction:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

---

## 7. Security & Privacy Design

- **Read-Only Local Data Access:** All local session transcripts and SQLite databases (`state.vscdb`) are accessed strictly read-only (`?mode=ro`).
- **Zero Credential Logging:** `sanitize_message()` automatically redacts Anthropic (`sk-ant-...`), OpenAI (`sk-...`, `eyJ...`), Google (`ya29...`), and `Bearer` tokens from all status strings, warnings, and error messages.
- **No Telemetry:** The widget only communicates with official provider quota endpoints (`api.anthropic.com`, `chatgpt.com`, or localhost `language_server`) when `network_quota_enabled` is enabled in settings.

---

## 8. Credits & License

- Inspired by the open-source AI usage tracking community, including [`yusufipk/kde-agents-usage`](https://github.com/yusufipk/kde-agents-usage) (`serjor/kde-agents-usage` / `burakgon/ai-usage-kde-widget`) and `RanulfoMNeto/agent-usage`.
- Licensed under the [MIT License](LICENSE).
