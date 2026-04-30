"""Config loader: writes/reads the roaming application config.

First run writes a defaults file (auto-detecting PotPlayer path) so the
user has something to edit without reading the README.
"""
from __future__ import annotations

import configparser
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from .app_info import APP_NAME
from .paths import config_path


DEFAULTS = {
    "potplayer": {
        "potplayer_path": "",  # auto-detect if blank
    },
    "ffmpeg": {
        "ffmpeg_path": "ffmpeg",
        "ffprobe_path": "ffprobe",
        "mkvpropedit_path": "mkvpropedit",  # unused in v1 (kept for future)
    },
    "safety": {
        # keep_until_next_run | keep_forever | delete_immediately
        "backup_behavior": "keep_until_next_run",
    },
    "ui": {
        # auto | center_potplayer | bottom_right | cursor  (Phase 2 preview)
        "popup_position": "auto",
        # Hotkeys — human-readable format, e.g. "numpad 2", "ctrl+alt+r",
        # "alt+F12", "win+shift+j". The daemon binds these via RegisterHotKey
        # and only fires when PotPlayer is the foreground window.
        "rotation_hotkey": "ctrl+alt+numpad 2",
        "rename_hotkey": "ctrl+alt+numpad 4",
    },
    "meta": {
        "default_hotkey_note": (
            "Hotkeys are bound by THIS daemon (RegisterHotKey) and only "
            "trigger when a PotPlayer window has foreground focus."
        ),
    },
}


@dataclass
class Config:
    potplayer_path: str
    ffmpeg_path: str
    ffprobe_path: str
    mkvpropedit_path: str
    backup_behavior: str
    popup_position: str
    rotation_hotkey: str
    rename_hotkey: str


def _auto_detect_potplayer() -> str:
    candidates = [
        r"C:\Program Files\DAUM\PotPlayer\PotPlayerMini64.exe",
        r"C:\Program Files (x86)\DAUM\PotPlayer\PotPlayerMini64.exe",
        r"C:\Program Files\DAUM\PotPlayer\PotPlayerMini.exe",
        r"C:\Program Files (x86)\DAUM\PotPlayer\PotPlayerMini.exe",
    ]
    for c in candidates:
        if Path(c).is_file():
            return c
    return ""


def _app_dirs() -> list[Path]:
    dirs: list[Path] = []
    if getattr(sys, "frozen", False):
        dirs.append(Path(sys.executable).resolve().parent)
        bundle_dir = getattr(sys, "_MEIPASS", None)
        if bundle_dir:
            dirs.append(Path(bundle_dir).resolve())
    dirs.append(Path(__file__).resolve().parent.parent)
    return dirs


def _resolve_executable(value: str, fallback_name: str) -> str:
    raw = (value or "").strip() or fallback_name
    candidate = Path(raw)

    if candidate.is_file():
        return str(candidate.resolve())

    if candidate.parent != Path("."):
        return raw

    for app_dir in _app_dirs():
        bundled = app_dir / f"{raw}.exe"
        if bundled.is_file():
            return str(bundled.resolve())
        bundled = app_dir / raw
        if bundled.is_file():
            return str(bundled.resolve())

    try:
        found = shutil.which(raw)
    except OSError:
        found = None
    if found:
        try:
            return str(Path(found).resolve())
        except OSError:
            return found

    return raw


def _write_defaults(p: Path) -> None:
    cp = configparser.ConfigParser()
    for section, items in DEFAULTS.items():
        cp[section] = dict(items)
    cp["potplayer"]["potplayer_path"] = _auto_detect_potplayer()
    with p.open("w", encoding="utf-8") as f:
        f.write(f"# {APP_NAME} config. Edit and save, then restart the tray daemon.\n\n")
        cp.write(f)


def load_config() -> Config:
    p = config_path()
    if not p.exists():
        _write_defaults(p)

    cp = configparser.ConfigParser()
    cp.read(p, encoding="utf-8")

    def get(section: str, key: str) -> str:
        if cp.has_option(section, key):
            return cp.get(section, key)
        # Backwards-compat: older config files used [ui] hotkey for rotation.
        if section == "ui" and key == "rotation_hotkey" and cp.has_option("ui", "hotkey"):
            return cp.get("ui", "hotkey")
        return DEFAULTS[section][key]

    potplayer = get("potplayer", "potplayer_path") or _auto_detect_potplayer()
    ffmpeg = _resolve_executable(get("ffmpeg", "ffmpeg_path"), "ffmpeg")
    ffprobe = _resolve_executable(get("ffmpeg", "ffprobe_path"), "ffprobe")
    mkvpropedit = _resolve_executable(get("ffmpeg", "mkvpropedit_path"), "mkvpropedit")

    return Config(
        potplayer_path=potplayer,
        ffmpeg_path=ffmpeg,
        ffprobe_path=ffprobe,
        mkvpropedit_path=mkvpropedit,
        backup_behavior=get("safety", "backup_behavior"),
        popup_position=get("ui", "popup_position"),
        rotation_hotkey=get("ui", "rotation_hotkey"),
        rename_hotkey=get("ui", "rename_hotkey"),
    )


def save_hotkeys(rotation_hotkey: str, rename_hotkey: str) -> None:
    p = config_path()
    if not p.exists():
        _write_defaults(p)

    cp = configparser.ConfigParser()
    cp.read(p, encoding="utf-8")
    if not cp.has_section("ui"):
        cp.add_section("ui")
    cp.set("ui", "rotation_hotkey", rotation_hotkey)
    cp.set("ui", "rename_hotkey", rename_hotkey)

    with p.open("w", encoding="utf-8") as f:
        cp.write(f)
