"""User-facing notifications.

We use two channels:
- Native MessageBox (via ctypes, zero deps) for modal yes/no confirmations.
- Windows toasts via `winotify` for async success/error notices.

Both are silent-fail safe: if the toast library is missing, we fall back
to a non-blocking MessageBox with MB_SETFOREGROUND so the user still sees it.
"""
from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes

from .app_info import APP_NAME

# MessageBox flags
_MB_OK = 0x0
_MB_OKCANCEL = 0x1
_MB_YESNO = 0x4
_MB_ICONERROR = 0x10
_MB_ICONQUESTION = 0x20
_MB_ICONWARNING = 0x30
_MB_ICONINFO = 0x40
_MB_TOPMOST = 0x40000
_MB_SETFOREGROUND = 0x10000
_MB_SYSTEMMODAL = 0x1000

_IDOK = 1
_IDYES = 6

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_MessageBoxW = _user32.MessageBoxW
_MessageBoxW.argtypes = (wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT)
_MessageBoxW.restype = ctypes.c_int


def _messagebox(text: str, title: str, flags: int) -> int:
    return _MessageBoxW(None, text, title, flags | _MB_TOPMOST | _MB_SETFOREGROUND)


def confirm_yes_no(text: str, title: str = APP_NAME) -> bool:
    """Modal Yes/No dialog. Returns True if user clicks Yes."""
    rc = _messagebox(text, title, _MB_YESNO | _MB_ICONQUESTION | _MB_SYSTEMMODAL)
    return rc == _IDYES


def error(text: str, title: str = f"{APP_NAME} error") -> None:
    """Modal error dialog. Blocks until dismissed."""
    _messagebox(text, title, _MB_OK | _MB_ICONERROR)


def _toast_via_winotify(title: str, message: str, icon_path: str | None = None) -> bool:
    try:
        from winotify import Notification, audio
    except Exception:
        return False
    try:
        n = Notification(app_id=APP_NAME, title=title, msg=message, icon=icon_path or "")
        n.set_audio(audio.Default, loop=False)
        n.show()
        return True
    except Exception:
        return False


def toast(title: str, message: str, icon_path: str | None = None) -> None:
    """Non-blocking notification. Falls back to a non-modal MessageBox
    in a background thread if winotify is unavailable."""
    if _toast_via_winotify(title, message, icon_path):
        return
    # Fallback: push a non-modal OK MessageBox onto a worker thread.
    threading.Thread(
        target=_messagebox,
        args=(message, title, _MB_OK | _MB_ICONINFO),
        daemon=True,
    ).start()
