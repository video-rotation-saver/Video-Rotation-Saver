"""Generate raster assets used by PyInstaller and Inno Setup.

The editable masters live in assets/branding. This script creates the
Windows .ico and installer bitmaps without requiring external graphics tools.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "build" / "assets"


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        Path("C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf"),
        Path("C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf"),
    ]
    for p in candidates:
        if p.exists():
            return ImageFont.truetype(str(p), size)
    return ImageFont.load_default()


def _draw_icon(size: int) -> Image.Image:
    scale = size / 1024

    def xy(v: float) -> int:
        return round(v * scale)

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    d.rounded_rectangle((xy(64), xy(64), xy(960), xy(960)), radius=xy(178), fill=(6, 18, 38, 255))
    d.arc((xy(220), xy(190), xy(804), xy(774)), start=28, end=172, fill=(0, 212, 255, 255), width=max(2, xy(64)))
    d.polygon([(xy(748), xy(84)), (xy(860), xy(156)), (xy(734), xy(230))], fill=(255, 255, 255, 255))
    d.arc((xy(220), xy(250), xy(804), xy(834)), start=208, end=352, fill=(21, 101, 255, 255), width=max(2, xy(64)))
    d.polygon([(xy(276), xy(888)), (xy(164), xy(816)), (xy(290), xy(742))], fill=(255, 255, 255, 255))

    d.rounded_rectangle((xy(320), xy(356), xy(704), xy(668)), radius=xy(54), fill=(255, 255, 255, 255))
    d.rounded_rectangle((xy(374), xy(420), xy(650), xy(604)), radius=xy(36), fill=(7, 22, 44, 255))
    d.polygon([(xy(482), xy(452)), (xy(482), xy(572)), (xy(594), xy(512))], fill=(255, 255, 255, 255))
    return img


def _draw_gradient(size: tuple[int, int]) -> Image.Image:
    w, h = size
    img = Image.new("RGB", size)
    px = img.load()
    for y in range(h):
        for x in range(w):
            t = (x / max(1, w - 1) + y / max(1, h - 1)) / 2
            r = round(11 * (1 - t) + 2 * t)
            g = round(29 * (1 - t) + 9 * t)
            b = round(51 * (1 - t) + 19 * t)
            px[x, y] = (r, g, b)
    return img


def _draw_banner() -> Image.Image:
    img = _draw_gradient((1500, 420)).convert("RGBA")
    d = ImageDraw.Draw(img)
    icon = _draw_icon(276)
    img.alpha_composite(icon, (64, 72))
    d.text((390, 96), "VIDEO", font=_font(64, True), fill=(255, 255, 255, 255))
    d.text((390, 166), "ROTATION", font=_font(64, True), fill=(57, 169, 255, 255))
    d.text((390, 236), "SAVER", font=_font(64, True), fill=(255, 255, 255, 255))
    d.arc((920, -70, 1530, 540), start=205, end=330, fill=(21, 101, 255, 120), width=10)
    d.arc((980, 20, 1470, 510), start=205, end=330, fill=(0, 212, 255, 70), width=5)
    return img


def _draw_wizard_image() -> Image.Image:
    img = _draw_gradient((164, 314)).convert("RGBA")
    icon = _draw_icon(118)
    img.alpha_composite(icon, (23, 32))
    d = ImageDraw.Draw(img)
    d.text((21, 176), "VIDEO", font=_font(22, True), fill=(255, 255, 255, 255))
    d.text((21, 204), "ROTATION", font=_font(22, True), fill=(57, 169, 255, 255))
    d.text((21, 232), "SAVER", font=_font(22, True), fill=(255, 255, 255, 255))
    return img.convert("RGB")


def _draw_wizard_small() -> Image.Image:
    img = _draw_gradient((55, 55)).convert("RGBA")
    icon = _draw_icon(45)
    img.alpha_composite(icon, (5, 5))
    return img.convert("RGB")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    sizes = [256, 128, 64, 48, 32, 24, 16]
    icons = [_draw_icon(s) for s in sizes]
    icons[0].save(OUT / "app.ico", sizes=[(s, s) for s in sizes], append_images=icons[1:])
    _draw_icon(256).save(OUT / "app-icon.png")
    _draw_banner().save(OUT / "installer-banner.png")
    _draw_wizard_image().save(OUT / "wizard-image.bmp")
    _draw_wizard_small().save(OUT / "wizard-small-image.bmp")


if __name__ == "__main__":
    main()

