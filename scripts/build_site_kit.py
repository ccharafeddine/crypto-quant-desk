"""Build site-kit/: logo, social cards, screenshots (png + webp) and captions.

    python scripts/build_site_kit.py RAW_SCREENSHOT_DIR [LOGO_DIR]

RAW_SCREENSHOT_DIR holds the 1600x1000 grabs from capture_screenshots.py.
LOGO_DIR defaults to site-kit/logo (already populated in the repo). Needs Pillow,
which is not an app dependency: `pip install pillow`.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "site-kit"
FONTS = ROOT / "src" / "cqd" / "ui" / "assets" / "fonts"

BG = (14, 17, 20)  # Graphite bg #0E1114
SURFACE = (22, 27, 33)
BORDER = (45, 51, 59)
TEXT = (230, 234, 238)
MUTED = (139, 148, 158)
AMBER = (245, 158, 11)  # accent #F59E0B

CAPTIONS = {
    "01-portfolio": (
        "Portfolio overview",
        "Time-weighted equity curve against BTC buy-and-hold with deposits and "
        "withdrawals stripped out, risk at a glance and each holding's share of portfolio vol.",
    ),
    "02-markets": (
        "Markets",
        "Watchlist with sparklines, candlestick chart with volume, live depth ladder "
        "and time and sales, all from Kraken public data.",
    ),
    "03-trading": (
        "Trading",
        "Order ticket for every Kraken spot order type. Paper by default, a confirm "
        "dialog on every order, a max-order cap re-checked at send, and an audit log.",
    ),
    "04-signals": (
        "Signals and backtest",
        "A sized trend setup with an order-book timing read, a backtest net of fees "
        "and slippage next to its frictionless twin, and the live track record.",
    ),
    "05-risk-lab-var": (
        "Risk lab: VaR and backtests",
        "One-day VaR four ways and ES 97.5%, each backtested over a rolling 250-day "
        "window with Kupiec and Christoffersen tests.",
    ),
    "06-risk-lab-crashes": (
        "Risk lab: crash replays",
        "Today's book run through LUNA, FTX, the Aug 2024 carry unwind and the "
        "10 Oct 2025 cascade using real Kraken trades, plus a 30-day bootstrap NAV fan.",
    ),
}


def font(name: str, size: int, weight: str = "Regular") -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(str(FONTS / name), size)
    try:
        f.set_variation_by_name(weight)
    except (OSError, ValueError):
        pass
    return f


def rounded(img: Image.Image, radius: int) -> Image.Image:
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, *img.size), radius, fill=255)
    out = img.convert("RGBA")
    out.putalpha(mask)
    return out


def save_pair(img: Image.Image, stem: Path) -> None:
    img = img.convert("RGB")
    img.save(stem.with_suffix(".png"), optimize=True)
    img.save(stem.with_suffix(".webp"), quality=86, method=6)


def card(w: int, h: int, shot: Image.Image, logo: Image.Image) -> Image.Image:
    s = w / 1200  # everything is laid out at 1200 wide and scaled
    canvas = Image.new("RGB", (w, h), BG)
    draw = ImageDraw.Draw(canvas)
    # faint amber glow behind the screenshot
    glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse(
        (int(560 * s), int(120 * s), int(1300 * s), int(760 * s)), fill=(*AMBER, 38)
    )
    canvas.paste(
        glow.filter(ImageFilter.GaussianBlur(int(120 * s))),
        (0, 0),
        glow.filter(ImageFilter.GaussianBlur(int(120 * s))),
    )

    # screenshot, bleeding off the right edge
    sw = int(760 * s)
    sh = int(sw * shot.height / shot.width)
    pic = rounded(shot.resize((sw, sh), Image.LANCZOS), int(14 * s))
    frame = Image.new("RGBA", (sw + int(2 * s), sh + int(2 * s)), (*BORDER, 255))
    frame = rounded(frame, int(15 * s))
    x, y = int(520 * s), int((h - sh) / 2 + 40 * s)
    canvas.paste(frame, (x - int(s), y - int(s)), frame)
    canvas.paste(pic, (x, y), pic)

    # logo + text column
    lg = logo.resize((int(96 * s), int(96 * s)), Image.LANCZOS).convert("RGBA")
    canvas.paste(lg, (int(64 * s), int(80 * s)), lg)
    draw.text(
        (int(64 * s), int(210 * s)),
        "Crypto Quant",
        font=font("Inter.ttf", int(52 * s), "Bold"),
        fill=TEXT,
    )
    draw.text(
        (int(64 * s), int(272 * s)), "Desk", font=font("Inter.ttf", int(52 * s), "Bold"), fill=AMBER
    )
    sub = font("Inter.ttf", int(22 * s), "Regular")
    for i, line in enumerate(["Kraken spot terminal with", "desk-grade risk analytics"]):
        draw.text((int(64 * s), int(352 * s + i * 32 * s)), line, font=sub, fill=MUTED)
    mono = font("JetBrainsMono.ttf", int(17 * s), "Medium")
    tags = ["VaR backtests", "crash replays", "paper-first"]
    tx = int(64 * s)
    for tag in tags:
        tw = draw.textlength(tag, font=mono)
        box = (tx, int(450 * s), tx + int(tw + 24 * s), int(486 * s))
        draw.rounded_rectangle(box, int(6 * s), outline=BORDER, fill=SURFACE)
        draw.text((tx + int(12 * s), int(457 * s)), tag, font=mono, fill=TEXT)
        tx = box[2] + int(10 * s)
        if tx > int(470 * s):
            break
    draw.rectangle((int(64 * s), int(560 * s), int(112 * s), int(564 * s)), fill=AMBER)
    draw.text(
        (int(64 * s), int(578 * s)),
        "PySide6 · pandas · Kraken API",
        font=font("Inter.ttf", int(17 * s), "Medium"),
        fill=MUTED,
    )
    return canvas


def main() -> None:
    raw = Path(sys.argv[1])
    logo_src = Path(sys.argv[2]) if len(sys.argv) > 2 else KIT / "logo"
    (KIT / "logo").mkdir(parents=True, exist_ok=True)
    if logo_src.resolve() != (KIT / "logo").resolve():
        for f in logo_src.iterdir():
            if f.suffix in {".png", ".svg"}:
                shutil.copy2(f, KIT / "logo" / f.name)

    shots_dir = KIT / "screenshots"
    shots_dir.mkdir(parents=True, exist_ok=True)
    captions = []
    for stem, (title, text) in CAPTIONS.items():
        img = Image.open(raw / f"{stem}.png").convert("RGB")
        if img.size != (1600, 1000):
            img = img.resize((1600, 1000), Image.LANCZOS)
        save_pair(img, shots_dir / stem)
        captions.append(
            {
                "file": f"screenshots/{stem}.png",
                "webp": f"screenshots/{stem}.webp",
                "width": 1600,
                "height": 1000,
                "title": title,
                "caption": text,
                "alt": f"Crypto Quant Desk, {title.lower()} page (demo data)",
            }
        )
    (KIT / "captions.json").write_text(json.dumps(captions, indent=2) + "\n", encoding="utf-8")

    hero = Image.open(raw / "01-portfolio.png").convert("RGB")
    logo = Image.open(KIT / "logo" / "logo-512.png")
    (KIT / "card").mkdir(exist_ok=True)
    for w, h in ((1200, 675), (800, 450)):
        save_pair(card(w, h, hero, logo), KIT / "card" / f"card-{w}x{h}")
    print("site-kit written to", KIT)


if __name__ == "__main__":
    main()
