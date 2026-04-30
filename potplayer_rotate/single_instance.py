"""Process-wide single instance guards."""
from __future__ import annotations

import ctypes
from ctypes import wintypes

from .app_info import APP_ID

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
_kernel32.CreateMutexW.restype = wintypes.HANDLE
_kernel32.OpenMutexW.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR)
_kernel32.OpenMutexW.restype = wintypes.HANDLE
_kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
_kernel32.CloseHandle.restype = wintypes.BOOL

_ERROR_ALREADY_EXISTS = 183
_SYNCHRONIZE = 0x00100000

_handles: list[int] = []


def _name(kind: str) -> str:
    return f"Local\\{APP_ID}_{kind}"


def acquire(kind: str) -> bool:
    """Acquire a named mutex. Returns False when another instance has it."""
    handle = _kernel32.CreateMutexW(None, True, _name(kind))
    if not handle:
        return False
    if ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
        _kernel32.CloseHandle(handle)
        return False
    _handles.append(int(handle))
    return True


def is_running(kind: str) -> bool:
    handle = _kernel32.OpenMutexW(_SYNCHRONIZE, False, _name(kind))
    if not handle:
        return False
    _kernel32.CloseHandle(handle)
    return True
