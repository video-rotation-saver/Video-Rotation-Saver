"""Well-known paths for config, logs, and runtime state."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from .app_info import APP_ID, LEGACY_APP_ID


def appdata_dir() -> Path:
    """Roaming app data directory, created on demand."""
    base = os.environ.get("APPDATA")
    if not base:
        # Extremely unlikely on Windows, but don't crash.
        base = str(Path.home() / "AppData" / "Roaming")
    d = Path(base) / APP_ID
    d.mkdir(parents=True, exist_ok=True)
    return d


def localappdata_dir() -> Path:
    """Local app data directory, created on demand."""
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        base = str(Path.home() / "AppData" / "Local")
    d = Path(base) / APP_ID
    d.mkdir(parents=True, exist_ok=True)
    return d


def config_path() -> Path:
    p = appdata_dir() / "config.ini"
    legacy = Path(os.environ.get("APPDATA", "")) / LEGACY_APP_ID / "config.ini"
    if not p.exists() and legacy.is_file():
        shutil.copy2(legacy, p)
    return p


def log_path() -> Path:
    return localappdata_dir() / "log.txt"
