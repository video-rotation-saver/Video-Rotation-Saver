"""Enumerate file handles held by a given PID, as the bulletproof fallback
for determining which file PotPlayer has open.

Uses NtQuerySystemInformation(SystemExtendedHandleInformation) to walk
every handle in the system, filters to the target PID, duplicates each
handle into our process, then resolves it to a file path via
GetFinalPathNameByHandleW. Video extensions only.

CRITICAL: every kernel32/ntdll call has explicit argtypes/restype. On
x64 Windows, HANDLEs are 8 bytes; without argtypes ctypes defaults to
4-byte int, which silently truncates HANDLE values and breaks every
DuplicateHandle call. That was the original bug.

This is heavy: O(total-system-handles). We call it only when registry +
title parsing fail. Diagnostic logging can be enabled via
`find_video_file_handles(pid, debug=True)`.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from pathlib import Path
from typing import Iterable

from .logging_setup import get_logger

log = get_logger()

_ntdll = ctypes.WinDLL("ntdll")
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# --- Constants --------------------------------------------------------------

_STATUS_INFO_LENGTH_MISMATCH = 0xC0000004
_SystemExtendedHandleInformation = 64
_PROCESS_DUP_HANDLE = 0x0040
_DUPLICATE_SAME_ACCESS = 0x00000002
_FILE_TYPE_DISK = 0x0001
_INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value

# --- ctypes signatures (required for correct x64 behaviour) -----------------

_ntdll.NtQuerySystemInformation.argtypes = (
    ctypes.c_ulong,                        # SystemInformationClass
    ctypes.c_void_p,                       # SystemInformation buffer
    ctypes.c_ulong,                        # SystemInformationLength
    ctypes.POINTER(ctypes.c_ulong),        # ReturnLength
)
_ntdll.NtQuerySystemInformation.restype = ctypes.c_ulong  # NTSTATUS

_kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
_kernel32.OpenProcess.restype = wintypes.HANDLE

_kernel32.GetCurrentProcess.argtypes = ()
_kernel32.GetCurrentProcess.restype = wintypes.HANDLE

_kernel32.DuplicateHandle.argtypes = (
    wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE,
    ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD, wintypes.BOOL, wintypes.DWORD,
)
_kernel32.DuplicateHandle.restype = wintypes.BOOL

_kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
_kernel32.CloseHandle.restype = wintypes.BOOL

_kernel32.GetFileType.argtypes = (wintypes.HANDLE,)
_kernel32.GetFileType.restype = wintypes.DWORD

_kernel32.GetFinalPathNameByHandleW.argtypes = (
    wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD,
)
_kernel32.GetFinalPathNameByHandleW.restype = wintypes.DWORD


class _SYSTEM_HANDLE_TABLE_ENTRY_INFO_EX(ctypes.Structure):
    _fields_ = [
        ("Object", ctypes.c_void_p),
        ("UniqueProcessId", ctypes.c_void_p),
        ("HandleValue", ctypes.c_void_p),
        ("GrantedAccess", ctypes.c_ulong),
        ("CreatorBackTraceIndex", ctypes.c_ushort),
        ("ObjectTypeIndex", ctypes.c_ushort),
        ("HandleAttributes", ctypes.c_ulong),
        ("Reserved", ctypes.c_ulong),
    ]


# --- System handle table walk -----------------------------------------------

def _query_system_handle_info() -> bytes:
    size = 0x100000
    while True:
        buf = ctypes.create_string_buffer(size)
        ret_len = ctypes.c_ulong(0)
        status = _ntdll.NtQuerySystemInformation(
            _SystemExtendedHandleInformation, buf, size, ctypes.byref(ret_len)
        )
        if status == 0:
            return buf.raw[: ret_len.value]
        if status & 0xFFFFFFFF == _STATUS_INFO_LENGTH_MISMATCH:
            size *= 2
            if size > 0x10000000:
                raise OSError("NtQuerySystemInformation buffer kept growing")
            continue
        raise OSError(f"NtQuerySystemInformation failed: 0x{status & 0xFFFFFFFF:08X}")


def _iter_handles_for_pid(pid: int) -> Iterable[int]:
    raw = _query_system_handle_info()
    count = ctypes.c_size_t.from_buffer_copy(raw[:ctypes.sizeof(ctypes.c_size_t)]).value
    stride = ctypes.sizeof(_SYSTEM_HANDLE_TABLE_ENTRY_INFO_EX)
    # Count is followed by ULONG_PTR Reserved, then the array on 16-byte boundary.
    offset = 0x10
    for _ in range(count):
        entry = _SYSTEM_HANDLE_TABLE_ENTRY_INFO_EX.from_buffer_copy(raw[offset:offset + stride])
        offset += stride
        if entry.UniqueProcessId is None:
            continue
        if int(entry.UniqueProcessId) == pid:
            yield int(entry.HandleValue)


# --- Handle → path resolution ----------------------------------------------

def _resolve_handle_to_path(proc_handle: int, handle_value: int) -> tuple[str | None, str]:
    """Return (path, reason). `path` is the resolved file path or None.
    `reason` is a short tag describing why it was rejected (for logging)
    or 'ok' on success."""
    dup = wintypes.HANDLE()
    cur = _kernel32.GetCurrentProcess()
    ok = _kernel32.DuplicateHandle(
        wintypes.HANDLE(proc_handle),
        wintypes.HANDLE(handle_value),
        cur,
        ctypes.byref(dup),
        0,
        False,
        _DUPLICATE_SAME_ACCESS,
    )
    if not ok:
        return None, "dup-fail"
    try:
        ftype = _kernel32.GetFileType(dup)
        if ftype != _FILE_TYPE_DISK:
            return None, f"not-disk(type={ftype})"
        buf = ctypes.create_unicode_buffer(32768)
        n = _kernel32.GetFinalPathNameByHandleW(dup, buf, 32768, 0)
        if n == 0:
            return None, "no-final-path"
        path = buf.value
        if path.startswith("\\\\?\\UNC\\"):
            path = "\\\\" + path[len("\\\\?\\UNC\\"):]
        elif path.startswith("\\\\?\\"):
            path = path[4:]
        return path, "ok"
    finally:
        _kernel32.CloseHandle(dup)


VIDEO_EXTS = {
    ".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi", ".wmv",
    ".ts", ".m2ts", ".mts", ".flv", ".3gp", ".mpg", ".mpeg",
}


# --- Public API -------------------------------------------------------------

def find_video_file_handles(pid: int, debug: bool = False) -> list[str]:
    """Return absolute paths of any video files PID currently holds open.
    Pass `debug=True` to emit a per-handle trace into the log."""
    proc = _kernel32.OpenProcess(_PROCESS_DUP_HANDLE, False, pid)
    if not proc or int(proc) == _INVALID_HANDLE_VALUE:
        err = ctypes.get_last_error()
        log.warning("handle-enum: OpenProcess(pid=%d) failed (win32 err %d) — "
                    "try running the daemon elevated", pid, err)
        return []

    results: list[str] = []
    seen: set[str] = set()
    handle_ids = list(_iter_handles_for_pid(pid))
    stats = {
        "total": len(handle_ids),
        "dup-fail": 0,
        "not-disk": 0,
        "no-final-path": 0,
        "file-disk": 0,
        "video-match": 0,
        "nonvideo-ext": 0,
    }
    nonvideo_samples: list[str] = []

    try:
        for h in handle_ids:
            path, reason = _resolve_handle_to_path(int(proc), h)
            if path is None:
                if reason == "dup-fail":
                    stats["dup-fail"] += 1
                elif reason.startswith("not-disk"):
                    stats["not-disk"] += 1
                elif reason == "no-final-path":
                    stats["no-final-path"] += 1
                continue
            stats["file-disk"] += 1
            try:
                ext = Path(path).suffix.lower()
            except Exception:
                continue
            if ext in VIDEO_EXTS:
                if path not in seen:
                    seen.add(path)
                    results.append(path)
                    stats["video-match"] += 1
            else:
                stats["nonvideo-ext"] += 1
                if debug and len(nonvideo_samples) < 40:
                    nonvideo_samples.append(f"({ext or '<none>'}) {path}")
    finally:
        _kernel32.CloseHandle(wintypes.HANDLE(proc))

    if debug:
        log.info("handle-enum pid=%d stats: %s", pid, stats)
        if nonvideo_samples:
            log.info("handle-enum pid=%d non-video disk handles (first %d):",
                     pid, len(nonvideo_samples))
            for s in nonvideo_samples:
                log.info("  %s", s)
    return results
