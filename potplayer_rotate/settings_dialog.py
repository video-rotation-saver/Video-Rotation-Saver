"""Small Windows settings dialogs."""
from __future__ import annotations

from .app_info import APP_NAME
from .hotkey import HotkeyParseError, parse_hotkey


def prompt_hotkeys(rotation_hotkey: str, rename_hotkey: str) -> tuple[str, str] | None:
    """Prompt for hotkeys. Returns None when cancelled."""
    import tkinter as tk
    from tkinter import messagebox, ttk

    result: list[tuple[str, str]] = []

    root = tk.Tk()
    root.title(f"{APP_NAME} Hotkeys")
    root.resizable(False, False)
    root.attributes("-topmost", True)

    pad = {"padx": 12, "pady": 6}
    frm = ttk.Frame(root, padding=12)
    frm.grid(row=0, column=0, sticky="nsew")

    ttk.Label(frm, text="Rotation hotkey").grid(row=0, column=0, sticky="w", **pad)
    rotation_var = tk.StringVar(value=rotation_hotkey)
    rotation_entry = ttk.Entry(frm, textvariable=rotation_var, width=32)
    rotation_entry.grid(row=0, column=1, sticky="ew", **pad)

    ttk.Label(frm, text="Rename hotkey").grid(row=1, column=0, sticky="w", **pad)
    rename_var = tk.StringVar(value=rename_hotkey)
    rename_entry = ttk.Entry(frm, textvariable=rename_var, width=32)
    rename_entry.grid(row=1, column=1, sticky="ew", **pad)

    hint = "Examples: ctrl+alt+numpad 2, ctrl+shift+r, alt+F12"
    ttk.Label(frm, text=hint).grid(row=2, column=0, columnspan=2, sticky="w", padx=12, pady=(4, 12))

    buttons = ttk.Frame(frm)
    buttons.grid(row=3, column=0, columnspan=2, sticky="e")

    def close() -> None:
        root.destroy()

    def save() -> None:
        rotation = rotation_var.get().strip()
        rename = rename_var.get().strip()
        try:
            parse_hotkey(rotation)
            parse_hotkey(rename)
        except HotkeyParseError as exc:
            messagebox.showerror(APP_NAME, f"Invalid hotkey: {exc}", parent=root)
            return
        if rotation.lower() == rename.lower():
            messagebox.showerror(APP_NAME, "Rotation and rename need different hotkeys.", parent=root)
            return
        result.append((rotation, rename))
        root.destroy()

    ttk.Button(buttons, text="Cancel", command=close).grid(row=0, column=0, padx=6)
    ttk.Button(buttons, text="Save", command=save).grid(row=0, column=1)

    root.bind("<Escape>", lambda _event: close())
    root.bind("<Return>", lambda _event: save())
    rotation_entry.focus_set()
    root.update_idletasks()
    width = root.winfo_width()
    height = root.winfo_height()
    x = (root.winfo_screenwidth() - width) // 2
    y = (root.winfo_screenheight() - height) // 2
    root.geometry(f"+{x}+{y}")
    root.mainloop()

    return result[0] if result else None


def prompt_filename(current_stem: str, extension: str = "") -> str | None:
    """Prompt for a new filename stem. Returns None when cancelled."""
    import tkinter as tk
    from tkinter import simpledialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        value = simpledialog.askstring(
            APP_NAME,
            f"New file name (extension stays {extension or 'unchanged'}):",
            initialvalue=current_stem,
            parent=root,
        )
    finally:
        root.destroy()
    if value is None:
        return None
    return value.strip()


def prompt_player_closed_actions() -> tuple[bool, bool]:
    """Ask what to do after PotPlayer closes: clear log, then close this app."""
    import tkinter as tk
    from tkinter import messagebox

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        clear_log = messagebox.askyesno(
            APP_NAME,
            "PotPlayer has closed.\n\nClear the Video Rotation Saver log?",
            parent=root,
        )
        close_app = messagebox.askyesno(
            APP_NAME,
            "Close Video Rotation Saver too?",
            parent=root,
        )
    finally:
        root.destroy()
    return clear_log, close_app
