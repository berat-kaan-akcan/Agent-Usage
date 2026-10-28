"""Theme palettes and formatting utilities for the Desktop Widget."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ThemePalette:
    name: str
    bg_primary: str
    bg_card: str
    bg_header: str
    text_primary: str
    text_secondary: str
    text_muted: str
    border: str
    accent_claude: str
    accent_codex: str
    accent_antigravity: str
    status_ok: str
    status_warn: str
    status_err: str
    progress_bg: str


DARK_PALETTE = ThemePalette(
    name="dark",
    bg_primary="#0F141C",
    bg_card="#18202C",
    bg_header="#131A24",
    text_primary="#F1F5F9",
    text_secondary="#CBD5E1",
    text_muted="#94A3B8",
    border="#2A364B",
    accent_claude="#D97757",
    accent_codex="#10A37F",
    accent_antigravity="#4285F4",
    status_ok="#22C55E",
    status_warn="#F59E0B",
    status_err="#EF4444",
    progress_bg="#263246",
)

LIGHT_PALETTE = ThemePalette(
    name="light",
    bg_primary="#F8FAFC",
    bg_card="#FFFFFF",
    bg_header="#F1F5F9",
    text_primary="#0F172A",
    text_secondary="#334155",
    text_muted="#64748B",
    border="#CBD5E1",
    accent_claude="#C45D3A",
    accent_codex="#0D8A6A",
    accent_antigravity="#2563EB",
    status_ok="#16A34A",
    status_warn="#D97706",
    status_err="#DC2626",
    progress_bg="#E2E8F0",
)


def get_palette(theme_name: str) -> ThemePalette:
    return LIGHT_PALETTE if theme_name == "light" else DARK_PALETTE
