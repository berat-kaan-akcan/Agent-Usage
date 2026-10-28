# Autonomous Development Progress — Agent Usage Widget

**Last Updated:** 2026-10-28T03:04:30+03:00  
**Current Phase:** ALL PHASES (1–6) COMPLETED

---

## Milestone Summary

### Phase 1 — Research (Completed)
- Audited the current CachyOS Linux (`7.2.11-1-cachyos-deckify` x86_64) + KDE Plasma Wayland environment, compilers (`g++ 16.2.1`, `cmake`, `ninja`, `Qt 6.12.0`), Python `3.14.8` (`sqlite3`, `gi` GTK 3/4, `AyatanaAppIndicator3`), and `makepkg`.
- Inspected the reference repositories (`yusufipk/kde-agents-usage` / `serjor/kde-agents-usage` / `burakgon/ai-usage-kde-widget` and `RanulfoMNeto/agent-usage` ecosystem equivalents), their licenses, and their data collection mechanisms.
- Verified real local and account-level data schemas for Claude Code, OpenAI Codex, and Google Antigravity.

### Phase 2 — Foundation (Completed)
- Created clean, modular project structure under `src/agent_usage/`, `native-qt/`, `tests/`, and `packaging/`.
- Implemented `src/agent_usage/core/models.py` (`ProviderSnapshot`, `TokenUsageStats`, `QuotaWindow`, `AuthState`, `QuotaSourceType`).
- Implemented `src/agent_usage/core/platform_info.py` (`detect_platform_environment`, `discover_provider_paths`, `AutostartManager` for Linux XDG `.desktop` and Windows Startup `.vbs`).
- Implemented `src/agent_usage/core/settings.py` (`WidgetSettings`, atomic `SettingsStore`, atomic `SnapshotCache`).
- Implemented `src/agent_usage/core/orchestrator.py` (`UsageOrchestrator` with background `ThreadPoolExecutor` workers).
- Implemented three Desktop Widget UI targets + CLI:
  - `native-qt/main.cpp` & `native-qt/CMakeLists.txt` (Native Qt6 C++17 Desktop Widget binary `build/agent-usage-qt`).
  - `src/agent_usage/ui/qt_widget.py` (Cross-platform `PySide6`/`PyQt6` Desktop Widget).
  - `src/agent_usage/ui/gtk_widget.py` (Linux `GTK 3.0` + `AyatanaAppIndicator3` Desktop Widget).
  - `src/agent_usage/cli.py` (`agent-usage --status`, `--json`, `--doctor`, `--no-network`, `--period`).

### Phase 3 — Providers (Completed)
- **Claude Code (`src/agent_usage/providers/claude.py`):**
  - Parses `~/.claude/projects/**/*.jsonl`, deduplicating streaming assistant chunks by `message.id` and aggregating `input_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`, and `output_tokens` across `five_hour`, `today`, `seven_day`, `thirty_day`, and `all_time` windows and per model.
  - Reads `~/.claude/.credentials.json` and queries `GET https://api.anthropic.com/api/oauth/usage` with `anthropic-beta: oauth-2025-04-20` and `User-Agent: claude-code/1.0.35`.
  - Clearly documents that 5-hour and 7-day subscription limits are shared across Claude Code, Claude Web, and Claude Desktop, and never fabricates individual web conversation tokens.
- **OpenAI Codex (`src/agent_usage/providers/codex.py`):**
  - Parses `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`, computing exact delta tokens from cumulative `total_token_usage` / `last_token_usage` and extracting embedded server `rate_limits` (`primary` 5h and `secondary` 7d windows).
  - Reads `~/.codex/auth.json`, distinguishing `OPENAI_API_KEY` pay-per-token billing (`api_billing`) from ChatGPT subscription quotas (`https://chatgpt.com/backend-api/wham/usage`).
- **Google Antigravity (`src/agent_usage/providers/antigravity.py`):**
  - Parses `~/.gemini/antigravity/brain/<id>/.system_generated/logs/transcript.jsonl` (`source == "MODEL"`, `input_tokens`, `cache_read_tokens`, `output_tokens`).
  - Queries localhost `language_server` Connect-RPC (`GetUserStatus`) when reachable and decodes verified Protobuf `antigravityUnifiedStateSync.userStatus` and `modelCredits` from `~/.config/Antigravity IDE/User/globalStorage/state.vscdb` in read-only SQLite mode (`?mode=ro`).
  - Verified live on this machine: extracts plan `Google AI Pro`, `116` available model credits, `1.78M+` recorded local tokens, and exact `remainingFraction` + UTC `resetTime` for all 10 models (`Gemini 3.8 Flash`, `Claude Opus 5.5`, `Claude Sonnet 5.5`, `GPT-OSS 120B`). Never infers remaining quota from local tokens.

### Phase 4 — Testing (Completed)
- Created 26 unit, fixture, and edge-case tests in `tests/` (`test_core.py`, `test_claude.py`, `test_codex.py`, `test_antigravity.py`, `test_orchestrator_and_cli.py`, `test_edge_cases.py`).
- All 26 tests pass (`Ran 26 tests in 0.186s — OK`).
- Verified `build/agent-usage-qt --self-test` passes (`Native Qt6 Agent Usage Widget self-test completed! Providers loaded: 3`).

### Phase 5 — Packaging (Completed)
- **Arch Linux / CachyOS:** Created `packaging/arch/PKGBUILD` and built `dist/agent-usage-widget-1.0.0-1-x86_64.pkg.tar.zst` (`158 KB`) using `makepkg` (including `build()`, `check()`, and `package()`).
- **Ubuntu / Debian:** Created `packaging/debian/build_deb.py` and built `dist/agent-usage-widget_1.0.0-1_amd64.deb` (`104 KB`).
- **Linux AppDir / AppImage:** Created `packaging/appimage/build_appimage.sh` and built portable `dist/AgentUsageWidget.AppDir` (verified `AppRun --self-test`).
- **Windows 10 / 11:** Created `packaging/windows/agent-usage-widget.spec` (PyInstaller), `packaging/windows/installer.iss` (Inno Setup), and `packaging/windows/build_windows.ps1`.

### Phase 6 — Final Review (Completed)
- Verified security & privacy: read-only SQLite access (`?mode=ro`), regex secret redaction (`sanitize_message`), atomic file writes (`tempfile.mkstemp` + `os.replace`), bounded network timeouts (`1.5s`–`5.0s`), and honest platform documentation in `README.md`.
