"""Generate the GTA VI-inspired channel avatar and banner (original artwork, no game assets).

    python -m gta.branding.make_assets  ->  gta/branding/out/avatar.png (800x800), banner.png (2560x1440)

The banner keeps the channel name and puts all text inside YouTube's safe area
(the centre 1546x423 region, which stays visible on every device).
"""
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUT = Path(__file__).resolve().parent / "out"
BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
PINK, PURPLE, CYAN, ORANGE, DARK = (255, 40, 180), (150, 60, 255), (0, 235, 255), (255, 140, 40), (10, 4, 22)


def _gradient(w, h):
    y = np.linspace(0, 1, h)[:, None, None]
    x = np.linspace(0, 1, w)[None, :, None]
    top = np.array(DARK)[None, None, :]
    mid = np.array(PURPLE)[None, None, :] * 0.35
    warm = np.array(ORANGE)[None, None, :] * 0.25
    base = top * (1 - y) + mid * y
    base = base + warm * np.clip(1 - np.abs(x - 0.85) * 2.2, 0, 1) * (y * 0.8)
    return Image.fromarray(np.clip(base, 0, 255).astype(np.uint8), "RGB")


def _glow_text(img, xy, text, font, color, blur=18):
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).text(xy, text, font=font, fill=color + (255,))
    glow = layer.filter(ImageFilter.GaussianBlur(blur))
    img.alpha_composite(glow)
    img.alpha_composite(layer)


def make_avatar(path):
    s = 800
    img = _gradient(s, s).convert("RGBA")
    ring = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(ring)
    for r, w, col in [(330, 26, PINK), (300, 8, CYAN)]:
        d.ellipse([s / 2 - r, s / 2 - r, s / 2 + r, s / 2 + r], outline=col + (255,), width=w)
    img.alpha_composite(ring.filter(ImageFilter.GaussianBlur(4)))
    img.alpha_composite(ring)
    font = ImageFont.truetype(BOLD, 250)
    w = ImageDraw.Draw(img).textlength("BR", font=font)
    _glow_text(img, ((s - w) / 2, 250), "BR", font, (255, 255, 255), blur=14)
    img.convert("RGB").save(path, "PNG", optimize=True)
    return path


def make_banner(path):
    W, H = 2560, 1440
    img = _gradient(W, H).convert("RGBA")
    grid = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grid)
    horizon = int(H * 0.62)
    for x in range(-W, W * 2, 140):
        gd.line([(W // 2, horizon), (x, H)], fill=CYAN + (70,), width=3)
    for i in range(14):
        yy = int(horizon + (H - horizon) * (i / 14) ** 1.7)
        gd.line([(0, yy), (W, yy)], fill=CYAN + (70,), width=3)
    img.alpha_composite(grid)
    # neon sun behind the safe area
    sun = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(sun).ellipse([W / 2 - 380, horizon - 420, W / 2 + 380, horizon + 340], fill=ORANGE + (120,))
    img.alpha_composite(sun.filter(ImageFilter.GaussianBlur(40)))
    # text inside the safe area: centre 1546x423
    big = ImageFont.truetype(BOLD, 150)
    small = ImageFont.truetype(BOLD, 62)
    d = ImageDraw.Draw(img)
    t1, t2 = "BOUNCE RUMBLE", "GTA VI SHORTS  ·  BEST MOMENTS  ·  LAUNCH COUNTDOWN"
    w1 = d.textlength(t1, font=big)
    w2 = d.textlength(t2, font=small)
    _glow_text(img, ((W - w1) / 2, 520), t1, big, (255, 255, 255), blur=22)
    _glow_text(img, ((W - w2) / 2, 760), t2, small, PINK, blur=10)
    img.convert("RGB").save(path, "PNG", optimize=True)
    return path


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    print(make_avatar(OUT / "avatar.png"))
    print(make_banner(OUT / "banner.png"))
