#!/usr/bin/env python3
"""Generate the display's chalkboard background: wallboard/web/display/chalk.jpg.

A slate base (#262727) with broad wiped-chalk smudges, long soft eraser arcs, faint grain
and a gentle vignette, 1080x1920 (the portrait board). Modelled on the chalkTexture()
canvas function of the approved month mockup (variant J) and the old DAKboard look.

Deterministic (fixed seed): re-running it gives the same image, so the committed JPEG can
be regenerated after a tweak.  Requires Pillow.

Usage:  python3 dev/make_chalk.py [--out wallboard/web/display/chalk.jpg]
"""

import argparse
import math
import random
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

W, H = 1080, 1920
SEED = 7
BASE = (38, 39, 39)  # #262727
CHALK = (235, 235, 230)
OUT = Path(__file__).resolve().parent.parent / "wallboard" / "web" / "display" / "chalk.jpg"


def smudges(rnd: random.Random, w: int, h: int) -> Image.Image:
    """Chalk coverage (L, 0..255 = opacity of the chalk colour) at w x h."""
    acc = Image.new("L", (w, h), 0)
    # Broad wiped areas: soft round blobs.
    for _ in range(26):
        x, y, r = rnd.random() * w, rnd.random() * h, 60 + rnd.random() * 170
        alpha = 0.035 + rnd.random() * 0.05
        layer = Image.new("L", (w, h), 0)
        ImageDraw.Draw(layer).ellipse((x - r * 0.6, y - r * 0.6, x + r * 0.6, y + r * 0.6), fill=round(alpha * 255))
        acc = ImageChops.add(acc, layer.filter(ImageFilter.GaussianBlur(r * 0.4)))
    # Eraser strokes: long soft arcs (quadratic curves), drawn in batches and softened.
    for _batch in range(7):
        layer = Image.new("L", (w, h), 0)
        for _ in range(10):
            stroke = Image.new("L", (w, h), 0)
            alpha = 0.015 + rnd.random() * 0.03
            width = 8 + rnd.random() * 34
            x, y = rnd.random() * w, rnd.random() * h
            length, angle = 80 + rnd.random() * 260, -0.5 + rnd.random() * 1.0
            cx = x + math.cos(angle) * length / 2
            cy = y + math.sin(angle) * length / 2 + (rnd.random() - 0.5) * 60
            ex, ey = x + math.cos(angle) * length, y + math.sin(angle) * length
            pts = []
            for i in range(25):
                t = i / 24
                pts.append(((1 - t) ** 2 * x + 2 * (1 - t) * t * cx + t * t * ex,
                            (1 - t) ** 2 * y + 2 * (1 - t) * t * cy + t * t * ey))
            draw = ImageDraw.Draw(stroke)
            draw.line(pts, fill=round(alpha * 255), width=round(width), joint="curve")
            for px, py in (pts[0], pts[-1]):  # round caps
                draw.ellipse((px - width / 2, py - width / 2, px + width / 2, py + width / 2), fill=round(alpha * 255))
            layer = ImageChops.add(layer, stroke)
        acc = ImageChops.add(acc, layer.filter(ImageFilter.GaussianBlur(3)))
    return acc


def grain(rnd: random.Random, w: int, h: int, amplitude: float) -> Image.Image:
    """Uniform noise centred on 128 (+-amplitude), deterministic from rnd."""
    noise = Image.frombytes("L", (w, h), rnd.randbytes(w * h))
    return noise.point(lambda v: round(128 + (v - 128) * amplitude / 128))


def vignette(w: int, h: int) -> Image.Image:
    """Darkening mask (L): 0 in the middle, up to ~35 % black towards the corners."""
    radial = Image.radial_gradient("L").resize((w, h), Image.BICUBIC)  # 0 centre .. 255 corners

    def curve(v: int) -> int:
        t = min(1.0, max(0.0, (v / 255 - 0.3) / 0.7))
        return round(0.35 * t ** 1.4 * 255)

    return radial.point(curve)


def make(seed: int = SEED) -> Image.Image:
    rnd = random.Random(seed)
    # Smudges at half size (they are soft anyway), then scaled up.
    cover = smudges(rnd, W // 2, H // 2).resize((W, H), Image.BICUBIC)
    img = Image.composite(Image.new("RGB", (W, H), CHALK), Image.new("RGB", (W, H), BASE), cover)
    noise = grain(rnd, W, H, 5).filter(ImageFilter.GaussianBlur(0.6))
    img = ImageChops.add(img, Image.merge("RGB", [noise] * 3), scale=1, offset=-128)
    img = Image.composite(Image.new("RGB", (W, H), (0, 0, 0)), img, vignette(W, H))
    return img


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--quality", type=int, default=80)
    args = ap.parse_args()
    make().save(args.out, "JPEG", quality=args.quality, optimize=True, progressive=True)
    print(f"{args.out}: {args.out.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
