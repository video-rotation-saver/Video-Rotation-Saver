"""PotPlayer integration: anchor-based per-HWND flow.

Every operation pins down a specific PotPlayer window at trigger time and
addresses everything at that HWND. We do NOT search for "a PotPlayer
window" in the middle of an operation — that would break when the user
runs multiple instances playing different files.

The flow in practice:

    anchor = build_anchor_from_foreground()          # returns None if
                                                     # foreground isn't
                                                     # PotPlayer
    if anchor is None:
        return silently

    state = snapshot_state(anchor)                   # title, position,
                                                     # total, play status,
                                                     # resolved file path

    send IPC to anchor.hwnd (close, seek, ...) — never to a re-found HWND

    run ffmpeg on state.file_path

    if not is_window_alive(anchor.hwnd):
        log and abort the reopen — user closed that instance
    else:
        post_file_drop(anchor.hwnd, new_path)        # HWND-scoped reopen
        wait briefly, seek_ms(anchor.hwnd, resume_ms)

Path detection uses registry + title, with a PID-scoped handle-enum
fallback so two instances playing same-basename files in different folders
still resolve correctly.

No documented PotPlayer IPC opcode returns the current file path, so we
don't try to query it directly.
"""
from __future__ import annotations

import ctypes
import os
import re
import subprocess
import time
import winreg
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

from .logging_setup import get_logger

log = get_logger()

# --- Win32 plumbing ---------------------------------------------------------

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_user32.FindWindowW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR)
_user32.FindWindowW.restype = wintypes.HWND

_user32.GetWindowTextLengthW.argtypes = (wintypes.HWND,)
_user32.GetWindowTextLengthW.restype = ctypes.c_int

_user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
_user32.GetWindowTextW.restype = ctypes.c_int

_user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
_user32.GetWindowThreadProcessId.restype = wintypes.DWORD

_user32.GetForegroundWindow.restype = wintypes.HWND
_user32.IsWindow.argtypes = (wintypes.HWND,)
_user32.IsWindow.restype = wintypes.BOOL

_user32.SendMessageW.argtypes = (wintypes.HWND, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM)
_user32.SendMessageW.restype = ctypes.c_long

_user32.PostMessageW.argtypes = (wintypes.HWND, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM)
_user32.PostMessageW.restype = wintypes.BOOL

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
_kernel32.OpenProcess.restype = wintypes.HANDLE

_kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
_kernel32.CloseHandle.restype = wintypes.BOOL

_kernel32.QueryFullProcessImageNameW.argtypes = (
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD))
_kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL

_kernel32.GlobalAlloc.argtypes = (wintypes.UINT, ctypes.c_size_t)
_kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
_kernel32.GlobalLock.argtypes = (wintypes.HGLOBAL,)
_kernel32.GlobalLock.restype = ctypes.c_void_p
_kernel32.GlobalUnlock.argtypes = (wintypes.HGLOBAL,)
_kernel32.GlobalUnlock.restype = wintypes.BOOL

# --- PotPlayer opcodes ------------------------------------------------------

_WM_USER = 0x0400
_WM_DROPFILES = 0x0233

POT_GET_TOTAL_TIME = 0x5002
POT_GET_CURRENT_TIME = 0x5004
POT_SET_CURRENT_TIME = 0x5005
POT_GET_PLAY_STATUS = 0x5006   # -1 stopped, 1 paused, 2 playing
POT_SET_PLAY_STATUS = 0x5007   # 0 toggle, 1 pause, 2 play
POT_SET_PLAY_CLOSE = 0x5009

_POTPLAYER_EXE_NAMES = {"potplayermini64.exe", "potplayermini.exe"}


# --- Data types -------------------------------------------------------------

@dataclass
class PotPlayerAnchor:
    """Snapshot of the target PotPlayer window at hotkey time. Everything
    downstream uses these fields and never re-searches."""
    hwnd: int
    pid: int
    exe_basename: str
    title: str

    def is_alive(self) -> bool:
        return bool(_user32.IsWindow(self.hwnd))


@dataclass
class PotPlayerState:
    anchor: PotPlayerAnchor
    file_path: Path | None
    position_ms: int | None
    total_ms: int | None
    play_status: int | None  # -1 stopped, 1 paused, 2 playing, None if IPC failed

    @property
    def has_file(self) -> bool:
        return self.file_path is not None


class PotPlayerNotFound(Exception):
    pass


# --- Process identity -------------------------------------------------------

def _process_exe_basename(pid: int) -> str | None:
    h = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None
    try:
        buf = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(buf))
        ok = _kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))
        if not ok:
            return None
        return os.path.basename(buf.value)
    finally:
        _kernel32.CloseHandle(h)


def is_potplayer_pid(pid: int) -> bool:
    base = _process_exe_basename(pid)
    if not base:
        return False
    return base.lower() in _POTPLAYER_EXE_NAMES


# --- Anchor construction ----------------------------------------------------

def _window_pid(hwnd: int) -> int:
    pid = wintypes.DWORD(0)
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def _window_title(hwnd: int) -> str:
    length = _user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    _user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def build_anchor_from_hwnd(hwnd: int) -> PotPlayerAnchor | None:
    """Return an anchor if `hwnd` belongs to a PotPlayer process, else None."""
    if not hwnd or not _user32.IsWindow(hwnd):
        return None
    pid = _window_pid(hwnd)
    if not pid:
        return None
    exe = _process_exe_basename(pid)
    if not exe or exe.lower() not in _POTPLAYER_EXE_NAMES:
        return None
    return PotPlayerAnchor(hwnd=int(hwnd), pid=pid, exe_basename=exe, title=_window_title(hwnd))


def build_anchor_from_foreground() -> PotPlayerAnchor | None:
    """The daemon's hotkey-time anchor: pin to whatever's focused right now."""
    hwnd = _user32.GetForegroundWindow()
    return build_anchor_from_hwnd(hwnd)


def build_anchor_best_effort_any() -> PotPlayerAnchor | None:
    """CLI debugging helper: if foreground isn't PotPlayer, fall back to
    any PotPlayer window found via FindWindowW. NOT used by the daemon."""
    a = build_anchor_from_foreground()
    if a is not None:
        return a
    for cls in ("PotPlayer64", "PotPlayer"):
        hwnd = _user32.FindWindowW(cls, None)
        if hwnd:
            return build_anchor_from_hwnd(int(hwnd))
    return None


def has_any_potplayer_window() -> bool:
    """Return True when at least one visible PotPlayer top-level window exists."""
    for cls in ("PotPlayer64", "PotPlayer"):
        hwnd = _user32.FindWindowW(cls, None)
        if hwnd and build_anchor_from_hwnd(int(hwnd)) is not None:
            return True
    return False


# --- IPC (all take HWND explicitly) -----------------------------------------

def _ipc_get(hwnd: int, opcode: int) -> int:
    return int(_user32.SendMessageW(hwnd, _WM_USER, opcode, 0))


def _ipc_set(hwnd: int, opcode: int, value: int) -> None:
    _user32.SendMessageW(hwnd, _WM_USER, opcode, value)


def get_position_ms(hwnd: int) -> int:
    return _ipc_get(hwnd, POT_GET_CURRENT_TIME)


def get_total_ms(hwnd: int) -> int:
    return _ipc_get(hwnd, POT_GET_TOTAL_TIME)


def get_play_status(hwnd: int) -> int:
    return _ipc_get(hwnd, POT_GET_PLAY_STATUS)


def pause(hwnd: int) -> None:
    _ipc_set(hwnd, POT_SET_PLAY_STATUS, 1)


def play(hwnd: int) -> None:
    _ipc_set(hwnd, POT_SET_PLAY_STATUS, 2)


def close_current_file(hwnd: int) -> None:
    _ipc_set(hwnd, POT_SET_PLAY_CLOSE, 0)


def seek_ms(hwnd: int, ms: int) -> None:
    _ipc_set(hwnd, POT_SET_CURRENT_TIME, ms)


# --- Title parsing ----------------------------------------------------------

_TITLE_STRIP_SUFFIXES = (
    " - PotPlayer",
    " - PotPlayer 64",
    " - PotPlayer (64-bit)",
)


def parse_basename_from_title(title: str) -> str | None:
    if not title:
        return None
    t = title.strip()
    for suf in _TITLE_STRIP_SUFFIXES:
        if t.endswith(suf):
            t = t[: -len(suf)].strip()
            break
    t = re.sub(r"\s*\[\s*\d+\s*/\s*\d+\s*\]\s*$", "", t)
    return t or None


# --- Registry RememberFiles -------------------------------------------------

# Hardcoded fallbacks used only if dynamic DAUM-child enumeration fails.
_REG_SUBKEY_FALLBACKS = (
    r"Software\DAUM\PotPlayerMini64\RememberFiles",
    r"Software\DAUM\PotPlayerMini\RememberFiles",
    r"Software\DAUM\PotPlayer\RememberFiles",
    r"Software\DAUM\PotPlayer64\RememberFiles",
)


def _candidate_remember_file_subkeys() -> list[str]:
    """Every plausible RememberFiles subkey. Starts by enumerating every
    child of HKCU\\Software\\DAUM — so any PotPlayer variant (Mini64,
    Mini, plain, PotPlayer64, future names) is discovered automatically —
    then unions in hardcoded fallbacks in case DAUM itself can't be opened.
    """
    subkeys: list[str] = []
    try:
        hk = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\DAUM")
    except OSError:
        hk = None
    if hk is not None:
        try:
            i = 0
            while True:
                try:
                    child = winreg.EnumKey(hk, i)
                except OSError:
                    break
                i += 1
                subkeys.append(f"Software\\DAUM\\{child}\\RememberFiles")
        finally:
            winreg.CloseKey(hk)
    for hard in _REG_SUBKEY_FALLBACKS:
        if hard not in subkeys:
            subkeys.append(hard)
    return subkeys


def _enumerate_remember_files() -> list[str]:
    """All full paths found across PotPlayer's remember-files registry keys.
    Logs per-subkey diagnostics at INFO level so we can see exactly what
    the registry held at resolution time."""
    results: list[str] = []
    candidates = _candidate_remember_file_subkeys()
    log.info("remember-files: scanning %d candidate subkey(s)", len(candidates))
    for sub in candidates:
        try:
            hk = winreg.OpenKey(winreg.HKEY_CURRENT_USER, sub)
        except OSError:
            log.info("remember-files: HKCU\\%s — NOT present", sub)
            continue
        found_here: list[str] = []
        try:
            i = 0
            while True:
                try:
                    _name, value, _kind = winreg.EnumValue(hk, i)
                except OSError:
                    break
                i += 1
                if isinstance(value, str):
                    path, _sep, _rest = value.partition("*")
                    if path:
                        found_here.append(path)
        finally:
            winreg.CloseKey(hk)
        log.info("remember-files: HKCU\\%s — %d entries", sub, len(found_here))
        for j, p in enumerate(found_here):
            log.info("  [%02d] %s", j, p)
        results.extend(found_here)
    return results


def _log_daum_subkeys() -> None:
    """One-shot dump of HKCU\\Software\\DAUM child keys so we can see which
    PotPlayer flavour wrote registry state on this install."""
    try:
        hk = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\DAUM")
    except OSError:
        log.info("DAUM subkeys: HKCU\\Software\\DAUM not present")
        return
    names: list[str] = []
    try:
        i = 0
        while True:
            try:
                names.append(winreg.EnumKey(hk, i))
            except OSError:
                break
            i += 1
    finally:
        winreg.CloseKey(hk)
    log.info("DAUM subkeys under HKCU\\Software\\DAUM: %r", names)


# --- File path detection ----------------------------------------------------

def resolve_file_for_anchor(anchor: PotPlayerAnchor) -> Path | None:
    log.info("resolve: hwnd=%d pid=%d raw title=%r", anchor.hwnd, anchor.pid, anchor.title)
    basename = parse_basename_from_title(anchor.title)
    log.info("resolve: parsed title basename=%r", basename)

    _log_daum_subkeys()
    reg_entries = _enumerate_remember_files()

    # Registry pass — scan dumped entries for a basename match.
    reg_hit: str | None = None
    if basename:
        bn = basename.casefold()
        for path in reg_entries:
            try:
                if Path(path).name.casefold() == bn:
                    reg_hit = path
                    log.info("resolve: registry basename match => %s", path)
                    break
            except Exception:
                continue
        if reg_hit is None:
            log.info("resolve: no registry entry matched basename %r (scanned %d)",
                     basename, len(reg_entries))

    # Handle enumeration (always, so we can see what's under the PID).
    try:
        from .handle_enum import find_video_file_handles
        handles = find_video_file_handles(anchor.pid, debug=True)
    except Exception as e:
        log.warning("resolve: handle enum for pid=%d raised: %s", anchor.pid, e)
        handles = []
    log.info("resolve: handle enum pid=%d returned %d video handles",
             anchor.pid, len(handles))
    for j, h in enumerate(handles):
        log.info("  [%02d] %s", j, h)

    # Branch A: registry hit with existing file.
    if reg_hit and Path(reg_hit).exists():
        p = Path(reg_hit)
        for h in handles:
            if os.path.normcase(os.path.abspath(h)) == os.path.normcase(os.path.abspath(str(p))):
                log.info("resolve: registry + handle confirm => %s", p)
                return p
        if handles and basename:
            for h in handles:
                if Path(h).name.casefold() == basename.casefold():
                    log.info("resolve: registry disagreed with handles; trusting handle => %s", h)
                    return Path(h)
        log.info("resolve: registry hit accepted (no handle confirmation) => %s", p)
        return p
    if reg_hit and not Path(reg_hit).exists():
        log.warning("resolve: registry hit but path does not exist on disk: %s", reg_hit)

    # Branch B: handle-enum title match.
    if basename:
        bn = basename.casefold()
        for h in handles:
            if Path(h).name.casefold() == bn:
                log.info("resolve: handle-enum title match => %s", h)
                return Path(h)

    # Branch C: sole handle candidate.
    if len(handles) == 1:
        log.info("resolve: sole video handle for pid=%d => %s", anchor.pid, handles[0])
        return Path(handles[0])

    log.info(
        "resolve: UNRESOLVED — returning None "
        "(basename=%r, registry_hit=%r, registry_entries=%d, handle_candidates=%d)",
        basename, reg_hit, len(reg_entries), len(handles),
    )
    return None


# --- State snapshot ---------------------------------------------------------

def snapshot_state(anchor: PotPlayerAnchor) -> PotPlayerState:
    status = None
    pos = None
    total = None
    try:
        status = get_play_status(anchor.hwnd)
        if status != -1:
            pos = get_position_ms(anchor.hwnd)
            total = get_total_ms(anchor.hwnd)
    except Exception as e:
        log.warning("IPC probe failed for hwnd=%d: %s", anchor.hwnd, e)
    log.info("snapshot: hwnd=%d play_status=%r pos_ms=%r total_ms=%r",
             anchor.hwnd, status, pos, total)

    path = resolve_file_for_anchor(anchor) if status != -1 else None

    return PotPlayerState(
        anchor=anchor,
        file_path=path,
        position_ms=pos,
        total_ms=total,
        play_status=status,
    )


# --- Reopen via WM_DROPFILES (HWND-scoped) ----------------------------------

class _DROPFILES(ctypes.Structure):
    _fields_ = [
        ("pFiles", wintypes.DWORD),  # offset of file list from struct start
        ("pt_x", wintypes.LONG),
        ("pt_y", wintypes.LONG),
        ("fNC", wintypes.BOOL),
        ("fWide", wintypes.BOOL),
    ]


_GMEM_MOVEABLE = 0x0002
_GMEM_ZEROINIT = 0x0040


def post_file_drop(hwnd: int, path: Path) -> bool:
    """Simulate dropping `path` onto `hwnd`. PotPlayer reacts by loading
    the file. Crucially, this is scoped to a single HWND — unlike
    `/current` on the CLI, which hits whichever instance is reachable.
    Returns True on success."""
    if not _user32.IsWindow(hwnd):
        return False

    file_str = str(path) + "\0\0"   # double-null termination for DROPFILES file list
    file_bytes = file_str.encode("utf-16-le")
    hdr_size = ctypes.sizeof(_DROPFILES)
    total = hdr_size + len(file_bytes)

    hmem = _kernel32.GlobalAlloc(_GMEM_MOVEABLE | _GMEM_ZEROINIT, total)
    if not hmem:
        log.error("GlobalAlloc(%d) failed", total)
        return False

    ptr = _kernel32.GlobalLock(hmem)
    if not ptr:
        log.error("GlobalLock failed")
        return False
    try:
        df = _DROPFILES.from_address(ptr)
        df.pFiles = hdr_size
        df.pt_x = 0
        df.pt_y = 0
        df.fNC = 0
        df.fWide = 1
        ctypes.memmove(ptr + hdr_size, file_bytes, len(file_bytes))
    finally:
        _kernel32.GlobalUnlock(hmem)

    # PostMessage transfers ownership of hmem to the target window;
    # we must NOT GlobalFree it.
    ok = _user32.PostMessageW(hwnd, _WM_DROPFILES, wintypes.WPARAM(hmem), 0)
    if not ok:
        err = ctypes.get_last_error()
        log.error("PostMessage(WM_DROPFILES) failed: win32 err %d", err)
        return False
    return True


def wait_until_file_released(path: Path, timeout_s: float = 5.0) -> bool:
    """Poll until we can open `path` for exclusive read/write."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with path.open("r+b"):
                return True
        except PermissionError:
            time.sleep(0.1)
        except OSError:
            return True
    return False


def wait_until_playing(hwnd: int, timeout_s: float = 4.0) -> bool:
    """After a WM_DROPFILES, poll until POT_GET_PLAY_STATUS reports
    paused or playing (non-stopped) so we know a seek will land."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            st = get_play_status(hwnd)
        except Exception:
            st = -1
        if st in (1, 2):
            return True
        time.sleep(0.05)
    return False


# --- CLI-level launcher (for debugging / fallbacks only) --------------------

_CREATE_NO_WINDOW = 0x08000000
_DETACHED_PROCESS = 0x00000008


def launch_file_via_cli(potplayer_exe: str, path: Path, resume_seconds: float | None = None) -> None:
    """Launches PotPlayer via CLI with `/current`. Use only as a last
    resort — it doesn't pick a specific instance."""
    args = [potplayer_exe, str(path), "/current"]
    if resume_seconds is not None and resume_seconds > 0.1:
        args.append(f"/seek={resume_seconds:.3f}")
    log.info("launching via CLI: %s", args)
    subprocess.Popen(
        args,
        creationflags=_CREATE_NO_WINDOW | _DETACHED_PROCESS,
        close_fds=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
