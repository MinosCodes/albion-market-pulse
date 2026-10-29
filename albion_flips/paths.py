"""Filesystem locations that work both from source and inside a PyInstaller .exe.

- Bundled, read-only files (data/, assets/, config.example.json) live in BUNDLE_DIR.
- User files (config.json, .cache/) live in USER_DIR: next to the .exe when frozen,
  or the project root when running from source. Falls back to %LOCALAPPDATA% (or
  ~/.albion-market-pulse) when that folder is not writable.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))

if FROZEN:
    BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    _preferred_user_dir = Path(sys.executable).resolve().parent
else:
    BUNDLE_DIR = Path(__file__).resolve().parent.parent
    _preferred_user_dir = BUNDLE_DIR


def _writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def _resolve_user_dir() -> Path:
    if _writable(_preferred_user_dir):
        return _preferred_user_dir
    base = os.environ.get("LOCALAPPDATA")
    fallback = Path(base) / "AlbionMarketPulse" if base else Path.home() / ".albion-market-pulse"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


USER_DIR = _resolve_user_dir()
DATA_DIR = BUNDLE_DIR / "data"
ASSETS_DIR = BUNDLE_DIR / "assets"
CACHE_DIR = USER_DIR / ".cache"


def data_file(name: str) -> Path:
    return DATA_DIR / name


def asset_file(name: str) -> Path:
    return ASSETS_DIR / name
