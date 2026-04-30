"""Global hotkey listener backed by RegisterHotKey.

We run this on a dedicated thread with its own message pump. When the
combination fires, we spawn a worker thread that invokes the callback
(so long jobs don't block the pump).

If RegisterHotKey returns ERROR_HOTKEY_ALREADY_REGISTERED (1409), we
record it on the listener and keep running — the daemon surfaces this
via the tray icon.
"""
from __future__ import annotations

import ctypes
import threading
import time
from ctypes import wintypes
from typing import Callable

from .logging_setup import get_logger

log = get_logger()

_user32 = ctypes.WinDLL("user32", use_last_error=True)

_MOD_ALT = 0x0001
_MOD_CONTROL = 0x0002
_MOD_SHIFT = 0x0004
_MOD_WIN = 0x0008
_MOD_NOREPEAT = 0x4000

_WM_HOTKEY = 0x0312
_WM_QUIT = 0x0012
_WM_APP = 0x8000
_WM_APP_PAUSE = _WM_APP + 1
_WM_APP_RESUME = _WM_APP + 2

_WH_KEYBOARD_LL = 13
_WM_KEYDOWN = 0x0100
_WM_SYSKEYDOWN = 0x0104

_ERROR_HOTKEY_ALREADY_REGISTERED = 1409

_user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
_user32.GetAsyncKeyState.restype = ctypes.c_short

_user32.SetWindowsHookExW.argtypes = (
    ctypes.c_int,
    ctypes.c_void_p,
    wintypes.HINSTANCE,
    wintypes.DWORD,
)
_user32.SetWindowsHookExW.restype = wintypes.HHOOK

_user32.UnhookWindowsHookEx.argtypes = (wintypes.HHOOK,)
_user32.UnhookWindowsHookEx.restype = wintypes.BOOL

_user32.CallNextHookEx.argtypes = (wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
_user32.CallNextHookEx.restype = wintypes.LPARAM

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
_kernel32.GetModuleHandleW.restype = wintypes.HMODULE


class _KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


_HOOKPROC = ctypes.WINFUNCTYPE(wintypes.LPARAM, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)


def _vk_table() -> dict[str, int]:
    t: dict[str, int] = {}
    # Letters
    for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        t[c] = 0x41 + (ord(c) - ord("A"))
    # Digits on main row
    for c in "0123456789":
        t[c] = 0x30 + (ord(c) - ord("0"))
    # Function keys
    for i in range(1, 25):
        t[f"F{i}"] = 0x6F + i
    # Numpad digits (VK_NUMPAD0..VK_NUMPAD9 = 0x60..0x69)
    for i in range(10):
        t[f"NUMPAD{i}"] = 0x60 + i
        t[f"NUMPAD {i}"] = 0x60 + i
        t[f"NUM{i}"] = 0x60 + i
    # Numpad operators
    t["MULTIPLY"] = t["NUMPAD *"] = t["NUMPAD*"] = 0x6A
    t["ADD"] = t["NUMPAD +"] = t["NUMPAD+"] = 0x6B
    t["SUBTRACT"] = t["NUMPAD -"] = t["NUMPAD-"] = 0x6D
    t["DECIMAL"] = t["NUMPAD ."] = t["NUMPAD."] = 0x6E
    t["DIVIDE"] = t["NUMPAD /"] = t["NUMPAD/"] = 0x6F
    # Named keys
    t.update({
        "SPACE": 0x20, "TAB": 0x09, "ESC": 0x1B, "ESCAPE": 0x1B,
        "ENTER": 0x0D, "RETURN": 0x0D,
        "LEFT": 0x25, "RIGHT": 0x27, "UP": 0x26, "DOWN": 0x28,
        "HOME": 0x24, "END": 0x23, "PGUP": 0x21, "PGDN": 0x22,
        "INSERT": 0x2D, "INS": 0x2D, "DELETE": 0x2E, "DEL": 0x2E,
        "BACKSPACE": 0x08, "CAPSLOCK": 0x14,
    })
    return t


_VK_NAMES = _vk_table()


class HotkeyParseError(ValueError):
    pass


def _normalize_token(tok: str) -> str:
    tok = tok.strip().upper()
    # Collapse "NUMPAD 2" — "NUMPAD  2" — "NUM 2" internal spaces
    parts = tok.split()
    if len(parts) == 2 and parts[0] in ("NUMPAD", "NUM") and parts[1].isdigit():
        return f"NUMPAD{parts[1]}"
    return tok


def parse_hotkey(s: str) -> tuple[int, int]:
    """'ctrl+shift+R' -> (modifiers, vk). 'numpad 2' -> (MOD_NOREPEAT, VK_NUMPAD2)."""
    if not s:
        raise HotkeyParseError("empty hotkey")
    # Split on '+' but keep trimmed; allow a bare key (no modifier).
    raw_parts = [p for p in (x.strip() for x in s.split("+")) if p]
    if not raw_parts:
        raise HotkeyParseError(f"no tokens in {s!r}")

    parts = [_normalize_token(p) for p in raw_parts]
    mods = 0
    *prefix, key = parts
    for p in prefix:
        if p in ("CTRL", "CONTROL"):
            mods |= _MOD_CONTROL
        elif p == "SHIFT":
            mods |= _MOD_SHIFT
        elif p == "ALT":
            mods |= _MOD_ALT
        elif p in ("WIN", "LWIN", "SUPER"):
            mods |= _MOD_WIN
        else:
            raise HotkeyParseError(f"unknown modifier: {p!r}")
    if key not in _VK_NAMES:
        raise HotkeyParseError(f"unknown key: {key!r}")
    vk = _VK_NAMES[key]
    return mods | _MOD_NOREPEAT, vk


class HotkeyListener:
    """Registers multiple hotkeys on a dedicated thread. Each hotkey is
    mapped to its own worker callback.

        hl = HotkeyListener()
        hl.add("ctrl+alt+r", on_rotate)
        hl.add("ctrl+alt+n", on_rename)
        hl.start()
        ...
        hl.pause() / hl.resume() / hl.stop()
    """

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._tid: int = 0
        # id -> (spec, mods, vk, callback)
        self._registrations: list[tuple[int, str, int, int, Callable[[], None]]] = []
        self._next_id = 1
        self._paused = False
        self._registered_ids: set[int] = set()
        self._fallback_ids: set[int] = set()
        self._fallback_pressed: set[int] = set()
        self._hook: wintypes.HHOOK | None = None
        self._hook_proc = _HOOKPROC(self._keyboard_hook)
        # Per-spec error messages for ones that failed to register.
        self.failures: dict[str, str] = {}

    def add(self, spec: str, callback: Callable[[], None]) -> None:
        mods, vk = parse_hotkey(spec)
        hid = self._next_id
        self._next_id += 1
        self._registrations.append((hid, spec, mods, vk, callback))

    # --- lifecycle ---

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="HotkeyListener", daemon=True)
        self._thread.start()

    def pause(self) -> None:
        if self._tid:
            _user32.PostThreadMessageW(self._tid, _WM_APP_PAUSE, 0, 0)

    def resume(self) -> None:
        if self._tid:
            _user32.PostThreadMessageW(self._tid, _WM_APP_RESUME, 0, 0)

    def stop(self) -> None:
        if self._tid:
            _user32.PostThreadMessageW(self._tid, _WM_QUIT, 0, 0)
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=1.0)
            self._thread = None
            self._tid = 0

    # --- introspection ---

    @property
    def all_registered(self) -> bool:
        return len(self._registered_ids | self._fallback_ids) == len(self._registrations)

    @property
    def is_paused(self) -> bool:
        return self._paused

    def status_summary(self) -> str:
        if self._paused:
            return "Paused"
        if not self._registrations:
            return "No hotkeys configured"
        ok = len(self._registered_ids | self._fallback_ids)
        total = len(self._registrations)
        if ok == total:
            return "Running"
        return f"{ok}/{total} hotkeys claimed"

    # --- internal ---

    def _register_all(self) -> None:
        self.failures.clear()
        for hid, spec, mods, vk, _cb in self._registrations:
            if hid in self._registered_ids:
                continue
            if _user32.RegisterHotKey(None, hid, mods, vk):
                self._registered_ids.add(hid)
                log.info("hotkey %s registered (id=%d)", spec, hid)
            else:
                err = ctypes.get_last_error()
                if err == _ERROR_HOTKEY_ALREADY_REGISTERED:
                    self._fallback_ids.add(hid)
                    log.warning("%s already in use; using foreground keyboard hook fallback", spec)
                    continue
                else:
                    msg = f"RegisterHotKey({spec}) failed (win32 err {err})"
                self.failures[spec] = msg
                log.warning(msg)
        self._ensure_hook()

    def _unregister_all(self) -> None:
        for hid in list(self._registered_ids):
            _user32.UnregisterHotKey(None, hid)
        self._registered_ids.clear()
        self._fallback_ids.clear()
        self._fallback_pressed.clear()
        self._remove_hook()

    def _find_cb(self, hid: int) -> Callable[[], None] | None:
        for h, _spec, _m, _vk, cb in self._registrations:
            if h == hid:
                return cb
        return None

    def _ensure_hook(self) -> None:
        if not self._fallback_ids or self._hook:
            return
        hmod = _kernel32.GetModuleHandleW(None)
        hook = _user32.SetWindowsHookExW(_WH_KEYBOARD_LL, self._hook_proc, hmod, 0)
        if hook:
            self._hook = hook
            log.info("keyboard hook fallback installed for %d hotkey(s)", len(self._fallback_ids))
            return

        err = ctypes.get_last_error()
        for hid, spec, _mods, _vk, _cb in self._registrations:
            if hid in self._fallback_ids:
                self.failures[spec] = f"{spec} fallback hook failed (win32 err {err})"
        self._fallback_ids.clear()
        log.warning("keyboard hook fallback failed (win32 err %d)", err)

    def _remove_hook(self) -> None:
        if self._hook:
            _user32.UnhookWindowsHookEx(self._hook)
            self._hook = None

    def _modifiers_down(self, mods: int) -> bool:
        checks = (
            (_MOD_CONTROL, 0x11),
            (_MOD_SHIFT, 0x10),
            (_MOD_ALT, 0x12),
            (_MOD_WIN, 0x5B),
        )
        for bit, vk in checks:
            down = bool(_user32.GetAsyncKeyState(vk) & 0x8000)
            if bool(mods & bit) != down:
                return False
        return True

    def _keyboard_hook(self, n_code: int, w_param: int, l_param: int) -> int:
        try:
            if n_code >= 0 and not self._paused and w_param in (_WM_KEYDOWN, _WM_SYSKEYDOWN):
                info = ctypes.cast(l_param, ctypes.POINTER(_KBDLLHOOKSTRUCT)).contents
                vk = int(info.vkCode)
                for hid, _spec, mods, registered_vk, cb in self._registrations:
                    if hid not in self._fallback_ids or vk != registered_vk:
                        continue
                    if not self._modifiers_down(mods & ~_MOD_NOREPEAT):
                        continue
                    if hid in self._fallback_pressed:
                        continue
                    self._fallback_pressed.add(hid)
                    threading.Thread(target=cb, daemon=True).start()

            # MOD_NOREPEAT equivalent for fallback hooks: clear keys after a short
            # delay so holding the key does not spam operations.
            if self._fallback_pressed:
                threading.Thread(target=self._release_fallback_keys, daemon=True).start()
        except Exception:
            log.exception("keyboard hook fallback failed while handling a key")
        return int(_user32.CallNextHookEx(self._hook, n_code, w_param, l_param))

    def _release_fallback_keys(self) -> None:
        time.sleep(0.3)
        self._fallback_pressed.clear()

    def _run(self) -> None:
        kernel32 = ctypes.windll.kernel32
        self._tid = kernel32.GetCurrentThreadId()
        self._register_all()

        msg = wintypes.MSG()
        while True:
            ret = _user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret == 0 or ret == -1:
                break
            if msg.message == _WM_HOTKEY:
                cb = self._find_cb(int(msg.wParam))
                if cb:
                    threading.Thread(target=cb, daemon=True).start()
            elif msg.message == _WM_APP_PAUSE:
                self._unregister_all()
                self._paused = True
                log.info("hotkey listener paused")
            elif msg.message == _WM_APP_RESUME:
                self._paused = False
                self._register_all()
                log.info("hotkey listener resumed")

        self._unregister_all()
        log.info("hotkey listener exiting")
