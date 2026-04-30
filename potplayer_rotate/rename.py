"""Rename the current PotPlayer file and reopen it in the same window."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .app_info import APP_NAME
from .logging_setup import get_logger
from .notify import toast
from .settings_dialog import prompt_filename
from . import potplayer as pp

log = get_logger()

_INVALID_CHARS = set('<>:"/\\|?*')
_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


@dataclass
class RenameResult:
    ok: bool
    old_path: Path | None
    new_path: Path | None
    message: str


def _normalize_new_stem(raw: str, old_path: Path) -> str:
    new_stem = raw.strip().strip('"')
    if old_path.suffix and new_stem.casefold().endswith(old_path.suffix.casefold()):
        new_stem = new_stem[: -len(old_path.suffix)].rstrip()
    if not new_stem:
        raise ValueError("Filename cannot be blank.")
    if any(ch in _INVALID_CHARS for ch in new_stem):
        raise ValueError('Filename cannot contain: < > : " / \\ | ? *')
    if new_stem.endswith("."):
        raise ValueError("Filename cannot end with a period.")
    if new_stem.upper() in _RESERVED_NAMES:
        raise ValueError(f"{new_stem!r} is a reserved Windows filename.")
    return new_stem + old_path.suffix


def run_rename_flow(anchor: pp.PotPlayerAnchor) -> RenameResult:
    state = pp.snapshot_state(anchor)
    if state.play_status is None or state.play_status == -1 or not state.has_file:
        return RenameResult(
            False,
            None,
            None,
            "PotPlayer has focus but nothing is loaded, or its file could not be resolved.",
        )

    assert state.file_path is not None
    old_path = state.file_path
    requested = prompt_filename(old_path.stem, old_path.suffix)
    if requested is None:
        return RenameResult(False, old_path, None, "Rename cancelled.")

    try:
        new_name = _normalize_new_stem(requested, old_path)
    except ValueError as exc:
        return RenameResult(False, old_path, None, str(exc))

    new_path = old_path.with_name(new_name)
    if os.path.normcase(str(new_path)) == os.path.normcase(str(old_path)):
        return RenameResult(True, old_path, old_path, "Filename unchanged.")
    if new_path.exists():
        return RenameResult(False, old_path, None, f"Target already exists: {new_path.name}")

    resume_s = max(0.0, (state.position_ms or 0) / 1000.0)
    log.info("rename start hwnd=%d pid=%d old=%s new=%s resume=%.2fs",
             anchor.hwnd, anchor.pid, old_path, new_path, resume_s)

    pp.close_current_file(anchor.hwnd)
    if not pp.wait_until_file_released(old_path, timeout_s=5.0):
        return RenameResult(False, old_path, None, "PotPlayer did not release the file in time.")

    try:
        old_path.rename(new_path)
    except OSError as exc:
        log.exception("rename failed")
        return RenameResult(False, old_path, None, f"Rename failed: {exc}")

    if anchor.is_alive():
        dropped = pp.post_file_drop(anchor.hwnd, new_path)
        log.info("rename reopen: WM_DROPFILES posted -> %s", dropped)
        if dropped and pp.wait_until_playing(anchor.hwnd, timeout_s=4.0):
            if resume_s > 0.1:
                pp.seek_ms(anchor.hwnd, int(resume_s * 1000))
        else:
            log.warning("rename reopen via drop failed; using CLI fallback")
            from .config import load_config
            cfg = load_config()
            pp.launch_file_via_cli(cfg.potplayer_path, new_path, resume_s)

    toast(APP_NAME, f"Renamed to {new_path.name}")
    return RenameResult(True, old_path, new_path, "Renamed.")
