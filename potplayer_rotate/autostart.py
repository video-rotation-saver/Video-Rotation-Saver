"""Per-user startup integration."""
from __future__ import annotations

import subprocess
import sys
import time
import winreg
import os

from .app_info import APP_NAME
from .logging_setup import get_logger
from .single_instance import acquire, is_running

log = get_logger()

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_STARTUP_VALUE = "VideoRotationSaver"
_WATCH_VALUE = "VideoRotationSaverPotPlayerWatch"
_CREATE_NO_WINDOW = 0x08000000
_DETACHED_PROCESS = 0x00000008
_POTPLAYER_EXES = ("PotPlayerMini64.exe", "PotPlayerMini.exe")


def _exe_path() -> str:
    return sys.executable


def _watch_command() -> str:
    return f'"{_exe_path()}" --watch-potplayer'


def _startup_command() -> str:
    return f'"{_exe_path()}"'


def _child_env() -> dict[str, str]:
    env = os.environ.copy()
    # A one-file PyInstaller app needs this when it starts an independent copy
    # of itself; otherwise the child can inherit the parent's temp extraction
    # state and fail with "Failed to start embedded python interpreter".
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


def is_potplayer_autostart_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            value, _kind = winreg.QueryValueEx(key, _WATCH_VALUE)
    except OSError:
        return False
    return "--watch-potplayer" in str(value)


def is_windows_startup_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            value, _kind = winreg.QueryValueEx(key, _STARTUP_VALUE)
    except OSError:
        return False
    return str(value).strip().strip('"').lower() == _exe_path().lower()


def set_windows_startup_enabled(enabled: bool) -> None:
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            command = _startup_command()
            winreg.SetValueEx(key, _STARTUP_VALUE, 0, winreg.REG_SZ, command)
            log.info("Windows startup enabled: %s", command)
        else:
            try:
                winreg.DeleteValue(key, _STARTUP_VALUE)
                log.info("Windows startup disabled")
            except FileNotFoundError:
                pass

    if enabled and not is_windows_startup_enabled():
        raise RuntimeError("The Windows startup registry entry was not saved.")


def set_potplayer_autostart_enabled(enabled: bool) -> None:
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            command = _watch_command()
            winreg.SetValueEx(key, _WATCH_VALUE, 0, winreg.REG_SZ, command)
            log.info("PotPlayer start watcher enabled: %s", command)
        else:
            try:
                winreg.DeleteValue(key, _WATCH_VALUE)
                log.info("PotPlayer start watcher disabled")
            except FileNotFoundError:
                pass

    if enabled and not is_potplayer_autostart_enabled():
        raise RuntimeError("The watcher registry entry was not saved.")

    if enabled and not is_running("watcher"):
        subprocess.Popen(
            [_exe_path(), "--watch-potplayer"],
            env=_child_env(),
            creationflags=_CREATE_NO_WINDOW | _DETACHED_PROCESS,
            close_fds=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        log.info("PotPlayer start watcher launched for current session")


def _process_is_running(exe_name: str) -> bool:
    result = subprocess.run(
        ["tasklist", "/FI", f"IMAGENAME eq {exe_name}", "/NH"],
        capture_output=True,
        text=True,
        creationflags=_CREATE_NO_WINDOW,
        check=False,
    )
    return exe_name.lower() in result.stdout.lower()


def _potplayer_is_running() -> bool:
    return any(_process_is_running(exe) for exe in _POTPLAYER_EXES)


def run_potplayer_watcher() -> int:
    if not acquire("watcher"):
        return 0

    if not is_potplayer_autostart_enabled():
        log.info("PotPlayer watcher exiting because the startup entry is disabled")
        return 0

    log.info("%s PotPlayer watcher starting", APP_NAME)
    potplayer_was_running = _potplayer_is_running()
    if potplayer_was_running and not is_running("daemon"):
        _launch_daemon()

    while True:
        try:
            if not is_potplayer_autostart_enabled():
                log.info("PotPlayer watcher exiting because the startup entry was disabled")
                return 0
            running = _potplayer_is_running()
            if running and not potplayer_was_running:
                potplayer_was_running = True
                if not is_running("daemon"):
                    _launch_daemon()
            elif not running:
                potplayer_was_running = False
        except Exception:
            log.exception("PotPlayer watcher probe failed")
        time.sleep(3.0)


def _launch_daemon() -> None:
    log.info("PotPlayer detected; launching %s", APP_NAME)
    subprocess.Popen(
        [_exe_path()],
        env=_child_env(),
        creationflags=_CREATE_NO_WINDOW | _DETACHED_PROCESS,
        close_fds=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
