"""Tray daemon: boots config, starts hotkey listener, shows tray icon.

The rotation hotkey triggers an immediate +90° CW rotation of whichever
PotPlayer window is currently in the foreground. The rename hotkey prompts
for a new filename, renames the current file, and reopens it.

The hotkey listener uses RegisterHotKey (global). On fire, we verify that
the CURRENT foreground window is a PotPlayer process before doing
anything; otherwise we silently no-op. No log entry, no toast, no error —
just as if the hotkey hadn't been claimed. (Heads-up: bare-key hotkeys
like Numpad 2 will swallow the key even in other apps. Use modifiers if
that matters to you; see README.)
"""
from __future__ import annotations

import threading
import time

from .app_info import APP_NAME
from . import potplayer as pp
from .autostart import (
    is_potplayer_autostart_enabled,
    is_windows_startup_enabled,
    set_potplayer_autostart_enabled,
    set_windows_startup_enabled,
)
from .config import load_config, save_hotkeys
from .hotkey import HotkeyListener, HotkeyParseError
from .logging_setup import clear_logs, get_logger
from .notify import toast
from .rename import run_rename_flow
from .rotate import run_rotation_flow, session_state
from .single_instance import acquire
from .splash import show_startup_splash
from .tray import TrayApp

log = get_logger()


class Daemon:
    def __init__(self) -> None:
        self.cfg = load_config()
        self._last_rotation_ts = 0.0
        self._last_rename_ts = 0.0
        self._op_lock = threading.Lock()
        self._hotkey: HotkeyListener | None = None
        self._tray: TrayApp | None = None
        self._stop_event = threading.Event()
        self._player_was_present = pp.has_any_potplayer_window()
        self._player_closed_prompt_lock = threading.Lock()

    # --- tray-facing status -------------------------------------------------

    def status_text(self) -> str:
        if not self._hotkey:
            return "● Starting…"
        if self._hotkey.is_paused:
            return "⏸  Paused"
        summary = self._hotkey.status_summary()
        if summary == "Running":
            return f"●  Running  (rotate {self.cfg.rotation_hotkey} · rename {self.cfg.rename_hotkey})"
        return f"⚠  {summary}"

    def is_paused(self) -> bool:
        return bool(self._hotkey and self._hotkey.is_paused)

    def potplayer_autostart_enabled(self) -> bool:
        return is_potplayer_autostart_enabled()

    def windows_startup_enabled(self) -> bool:
        return is_windows_startup_enabled()

    # --- hotkey dispatch ----------------------------------------------------

    def _debounce(self, which: str) -> bool:
        """Return True if this hotkey firing should be processed."""
        with self._op_lock:
            now = time.monotonic()
            if which == "rotate":
                if now - self._last_rotation_ts < 0.3:
                    return False
                self._last_rotation_ts = now
            elif which == "rename":
                if now - self._last_rename_ts < 0.3:
                    return False
                self._last_rename_ts = now
            return True

    def _foreground_anchor_or_silent(self) -> pp.PotPlayerAnchor | None:
        """Per item #1: GetForegroundWindow → verify PotPlayer process.
        Silent no-op (no log, no toast) if not PotPlayer."""
        a = pp.build_anchor_from_foreground()
        return a

    def on_rotate(self) -> None:
        if not self._debounce("rotate"):
            return
        anchor = self._foreground_anchor_or_silent()
        if anchor is None:
            return  # silent per spec

        log.info("rotate fired: hwnd=%d pid=%d title=%r",
                 anchor.hwnd, anchor.pid, anchor.title)
        try:
            result = run_rotation_flow(anchor, delta_cw=90)
        except Exception:
            log.exception("rotation flow crashed")
            if self._tray:
                self._tray.notify("Rotation crashed — see log", APP_NAME)
            return

        if not result.ok:
            toast(APP_NAME, f"Rotation failed: {result.message}")
            return

        # Path A playlist note, one-time per session.
        st = session_state()
        playlist_note = ""
        if not st.playlist_note_shown:
            playlist_note = ("  Playlist note: custom curated playlists may "
                             "need reloading; folder playlists rebuild "
                             "automatically. (Shown once per session.)")
            st.playlist_note_shown = True

        toast(
            APP_NAME,
            f"{result.previous_rotation_cw}° → {result.applied_rotation_cw}° CW"
            + (f" · {result.new_path.name}" if result.new_path else "")
            + playlist_note,
        )

    def on_rename(self) -> None:
        if not self._debounce("rename"):
            return
        anchor = self._foreground_anchor_or_silent()
        if anchor is None:
            return
        log.info("rename hotkey fired")
        try:
            result = run_rename_flow(anchor)
        except Exception:
            log.exception("rename flow crashed")
            if self._tray:
                self._tray.notify("Rename crashed — see log", APP_NAME)
            return
        if not result.ok and result.message != "Rename cancelled.":
            toast(APP_NAME, f"Rename failed: {result.message}")

    # --- tray actions -------------------------------------------------------

    def toggle_pause(self) -> None:
        if not self._hotkey:
            return
        if self._hotkey.is_paused:
            self._hotkey.resume()
        else:
            self._hotkey.pause()

    def change_hotkeys(self) -> None:
        from .settings_dialog import prompt_hotkeys

        updated = prompt_hotkeys(self.cfg.rotation_hotkey, self.cfg.rename_hotkey)
        if not updated:
            return

        rotation_hotkey, rename_hotkey = updated
        save_hotkeys(rotation_hotkey, rename_hotkey)
        self.cfg = load_config()
        self._restart_hotkeys()

        if self._tray:
            if self._hotkey and self._hotkey.failures:
                lines = "\n".join(f" - {v}" for v in self._hotkey.failures.values())
                self._tray.notify(f"Some hotkeys could not be claimed:\n{lines}", APP_NAME)
            else:
                self._tray.notify("Hotkeys updated.", APP_NAME)
            self._tray.refresh()

    def toggle_potplayer_autostart(self) -> None:
        try:
            enabled = not is_potplayer_autostart_enabled()
            set_potplayer_autostart_enabled(enabled)
            if self._tray:
                self._tray.notify(
                    (
                        "Enabled. The watcher is running now and will start at Windows sign-in."
                        if enabled else
                        "PotPlayer start watcher disabled."
                    ),
                    APP_NAME,
                )
                self._tray.refresh()
        except Exception:
            log.exception("could not update PotPlayer start trigger")
            toast(APP_NAME, "Could not update PotPlayer start trigger. See log.")

    def toggle_windows_startup(self) -> None:
        try:
            enabled = not is_windows_startup_enabled()
            set_windows_startup_enabled(enabled)
            if self._tray:
                self._tray.notify(
                    "Will start when Windows starts." if enabled else "Windows startup disabled.",
                    APP_NAME,
                )
                self._tray.refresh()
        except Exception:
            log.exception("could not update Windows startup")
            toast(APP_NAME, "Could not update Windows startup. See log.")

    def shutdown(self) -> None:
        log.info("daemon shutting down")
        self._stop_event.set()
        if self._hotkey:
            self._hotkey.stop()

    def _watch_player_lifecycle(self) -> None:
        log.info("player lifecycle watcher starting present=%s", self._player_was_present)
        while not self._stop_event.wait(2.0):
            try:
                present = pp.has_any_potplayer_window()
            except Exception:
                log.exception("player lifecycle probe failed")
                continue

            if present:
                self._player_was_present = True
                continue

            if self._player_was_present:
                self._player_was_present = False
                threading.Thread(
                    target=self._handle_player_closed,
                    name="PlayerClosedPrompt",
                    daemon=True,
                ).start()

    def _handle_player_closed(self) -> None:
        if not self._player_closed_prompt_lock.acquire(blocking=False):
            return
        try:
            from .settings_dialog import prompt_player_closed_actions

            clear_log, close_app = prompt_player_closed_actions()
            if clear_log:
                clear_logs()
                get_logger().info("log cleared after PotPlayer closed")
            if close_app:
                log.info("closing app after PotPlayer closed")
                self.shutdown()
                if self._tray:
                    self._tray.stop()
        except Exception:
            log.exception("player closed prompt failed")
        finally:
            self._player_closed_prompt_lock.release()

    def _restart_hotkeys(self) -> bool:
        if self._hotkey:
            self._hotkey.stop()
        self._hotkey = HotkeyListener()
        try:
            self._hotkey.add(self.cfg.rotation_hotkey, self.on_rotate)
            self._hotkey.add(self.cfg.rename_hotkey, self.on_rename)
        except HotkeyParseError as e:
            from .notify import error as notify_error
            notify_error(f"Invalid hotkey in config.ini: {e}\n"
                         "Open %APPDATA%\\VideoRotationSaver\\config.ini and fix it.")
            self._hotkey = None
            return False
        self._hotkey.start()
        time.sleep(0.15)
        return True

    # --- boot ---------------------------------------------------------------

    def run(self) -> int:
        if not acquire("daemon"):
            return 0

        show_startup_splash()

        log.info("daemon starting version=%s cfg=%r", _version(), {
            "rotation_hotkey": self.cfg.rotation_hotkey,
            "rename_hotkey": self.cfg.rename_hotkey,
            "potplayer": self.cfg.potplayer_path,
            "ffmpeg": self.cfg.ffmpeg_path,
            "backup": self.cfg.backup_behavior,
        })

        if not self.cfg.potplayer_path:
            from .notify import error as notify_error
            notify_error(
                "Couldn't find PotPlayerMini64.exe.\n"
                "Install PotPlayer, or edit 'potplayer_path' in config.ini."
            )
            return 2

        if not self._restart_hotkeys():
            return 2

        self._tray = TrayApp(
            get_status_text=self.status_text,
            on_toggle_pause=self.toggle_pause,
            on_change_hotkeys=self.change_hotkeys,
            is_potplayer_autostart_enabled=self.potplayer_autostart_enabled,
            on_toggle_potplayer_autostart=self.toggle_potplayer_autostart,
            is_windows_startup_enabled=self.windows_startup_enabled,
            on_toggle_windows_startup=self.toggle_windows_startup,
            is_paused=self.is_paused,
            on_exit=self.shutdown,
        )

        threading.Thread(
            target=self._watch_player_lifecycle,
            name="PlayerLifecycle",
            daemon=True,
        ).start()

        if self._hotkey.failures:
            lines = "\n".join(f" • {v}" for v in self._hotkey.failures.values())
            self._tray.notify(
                f"Couldn't claim some hotkeys:\n{lines}\n"
                "Free the combos or edit config.ini and toggle Pause/Resume.",
                APP_NAME,
            )

        self._tray.run()  # blocks until Exit

        if self._hotkey:
            self._hotkey.stop()
        return 0


def _version() -> str:
    from . import __version__
    return __version__


def run_daemon() -> int:
    return Daemon().run()
