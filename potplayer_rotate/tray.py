"""System tray icon using pystray.

Menu items:
  - "● Running" / "⏸ Paused"  (indicator, non-clickable)
  - Pause / Resume hotkey       (toggle)
  - View log                    (open log.txt)
  - Exit
"""
from __future__ import annotations

import os
import threading
from typing import Callable

from .app_info import APP_ID, APP_NAME
from .paths import log_path
from .logging_setup import get_logger

log = get_logger()


def _make_icon_image():
    """Generate a branded 64x64 tray icon at runtime."""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((2, 2, 62, 62), radius=12, fill=(6, 18, 38, 255))
    d.arc((11, 11, 53, 53), start=28, end=172, fill=(0, 212, 255, 255), width=5)
    d.polygon([(49, 9), (59, 15), (48, 22)], fill=(255, 255, 255, 255))
    d.arc((11, 11, 53, 53), start=208, end=352, fill=(21, 101, 255, 255), width=5)
    d.polygon([(15, 55), (5, 49), (16, 42)], fill=(255, 255, 255, 255))
    d.rounded_rectangle((20, 22, 44, 42), radius=4, outline=(255, 255, 255, 255), width=4)
    d.polygon([(29, 27), (29, 37), (38, 32)], fill=(255, 255, 255, 255))
    return img


class TrayApp:
    """Thin wrapper around pystray.Icon with a mutable state indicator."""

    def __init__(
        self,
        get_status_text: Callable[[], str],
        on_toggle_pause: Callable[[], None],
        on_change_hotkeys: Callable[[], None],
        is_potplayer_autostart_enabled: Callable[[], bool],
        on_toggle_potplayer_autostart: Callable[[], None],
        is_windows_startup_enabled: Callable[[], bool],
        on_toggle_windows_startup: Callable[[], None],
        is_paused: Callable[[], bool],
        on_exit: Callable[[], None],
    ) -> None:
        import pystray
        from pystray import MenuItem as Item, Menu

        self._pystray = pystray
        self._get_status_text = get_status_text
        self._on_toggle_pause = on_toggle_pause
        self._on_change_hotkeys = on_change_hotkeys
        self._is_potplayer_autostart_enabled = is_potplayer_autostart_enabled
        self._on_toggle_potplayer_autostart = on_toggle_potplayer_autostart
        self._is_windows_startup_enabled = is_windows_startup_enabled
        self._on_toggle_windows_startup = on_toggle_windows_startup
        self._is_paused = is_paused
        self._on_exit = on_exit

        def status_label(_item):
            return self._get_status_text()

        def pause_label(_item):
            return "Resume hotkey" if self._is_paused() else "Pause hotkey"

        def potplayer_autostart_label(_item):
            if self._is_potplayer_autostart_enabled():
                return "Disable start when PotPlayer starts"
            return "Enable start when PotPlayer starts"

        def windows_startup_label(_item):
            if self._is_windows_startup_enabled():
                return "Disable start with Windows"
            return "Enable start with Windows"

        menu = Menu(
            Item(status_label, None, enabled=False),
            Menu.SEPARATOR,
            Item(pause_label, self._handle_pause),
            Item("Change hotkeys...", self._handle_change_hotkeys),
            Item(
                "Startup",
                Menu(
                    Item(windows_startup_label, self._handle_toggle_windows_startup),
                    Item(potplayer_autostart_label, self._handle_toggle_potplayer_autostart),
                ),
            ),
            Item("View log", self._handle_view_log),
            Menu.SEPARATOR,
            Item("Exit", self._handle_exit),
        )

        self._icon = pystray.Icon(
            name=APP_ID,
            icon=_make_icon_image(),
            title=APP_NAME,
            menu=menu,
        )

    # pystray's Icon.run blocks; run it on a dedicated thread so the daemon
    # main can orchestrate the hotkey listener, config reloads, etc.
    def run(self) -> None:
        self._icon.run()

    def run_threaded(self) -> threading.Thread:
        t = threading.Thread(target=self._icon.run, name="Tray", daemon=False)
        t.start()
        return t

    def notify(self, message: str, title: str = APP_NAME) -> None:
        try:
            self._icon.notify(message, title)
        except Exception:
            # pystray .notify can fail on some Windows builds; swallow it.
            log.exception("tray notify failed")

    def refresh(self) -> None:
        try:
            self._icon.update_menu()
        except Exception:
            pass

    def stop(self) -> None:
        self._icon.stop()

    # -- menu handlers --
    def _handle_pause(self, _icon=None, _item=None):
        self._on_toggle_pause()
        self.refresh()

    def _handle_change_hotkeys(self, _icon=None, _item=None):
        self._on_change_hotkeys()
        self.refresh()

    def _handle_toggle_potplayer_autostart(self, _icon=None, _item=None):
        self._on_toggle_potplayer_autostart()
        self.refresh()

    def _handle_toggle_windows_startup(self, _icon=None, _item=None):
        self._on_toggle_windows_startup()
        self.refresh()

    def _handle_view_log(self, _icon=None, _item=None):
        p = log_path()
        try:
            os.startfile(str(p))  # noqa: S606 — intentional; opens the log file
        except Exception as e:
            log.exception("could not open log: %s", e)

    def _handle_exit(self, _icon=None, _item=None):
        try:
            self._on_exit()
        finally:
            self._icon.stop()
