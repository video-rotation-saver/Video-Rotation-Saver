"""Startup splash screen."""
from __future__ import annotations

import threading
import time

from .app_info import APP_NAME


def show_startup_splash(duration_s: float = 1.4) -> None:
    """Show a short branded splash without delaying daemon startup too long."""
    thread = threading.Thread(target=_show, args=(duration_s,), name="Splash", daemon=True)
    thread.start()


def _show(duration_s: float) -> None:
    try:
        import tkinter as tk
        from PIL import Image, ImageDraw, ImageFont, ImageTk

        root = tk.Tk()
        root.overrideredirect(True)
        root.attributes("-topmost", True)

        width, height = 520, 220
        img = Image.new("RGB", (width, height), (7, 22, 44))
        px = img.load()
        for y in range(height):
            for x in range(width):
                t = (x / width + y / height) / 2
                px[x, y] = (
                    round(8 * (1 - t) + 2 * t),
                    round(25 * (1 - t) + 9 * t),
                    round(48 * (1 - t) + 18 * t),
                )

        d = ImageDraw.Draw(img)
        icon_box = (34, 48, 154, 168)
        d.rounded_rectangle(icon_box, radius=22, fill=(6, 18, 38), outline=(57, 169, 255), width=2)
        d.arc((54, 66, 134, 146), start=28, end=172, fill=(0, 212, 255), width=7)
        d.arc((54, 74, 134, 154), start=208, end=352, fill=(21, 101, 255), width=7)
        d.rounded_rectangle((80, 91, 124, 126), radius=7, outline=(255, 255, 255), width=4)
        d.polygon([(98, 99), (98, 119), (116, 109)], fill=(255, 255, 255))

        title_font = _font(ImageFont, 34, bold=True)
        sub_font = _font(ImageFont, 13)
        d.text((184, 70), "Video Rotation", font=title_font, fill=(255, 255, 255))
        d.text((184, 111), "Saver", font=title_font, fill=(57, 169, 255))
        d.text((187, 158), "Watching PotPlayer hotkeys", font=sub_font, fill=(196, 216, 235))

        photo = ImageTk.PhotoImage(img)
        label = tk.Label(root, image=photo, borderwidth=0)
        label.image = photo
        label.pack()

        x = (root.winfo_screenwidth() - width) // 2
        y = (root.winfo_screenheight() - height) // 2
        root.geometry(f"{width}x{height}+{x}+{y}")
        root.after(int(duration_s * 1000), root.destroy)
        root.mainloop()
        time.sleep(0.05)
    except Exception:
        return


def _font(image_font_module, size: int, bold: bool = False):
    from pathlib import Path

    candidates = [
        Path("C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf"),
        Path("C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf"),
    ]
    for path in candidates:
        if path.exists():
            return image_font_module.truetype(str(path), size)
    return image_font_module.load_default()
