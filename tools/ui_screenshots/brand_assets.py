#!/usr/bin/env python3
"""Render Flaria's share image and app icons into flaria-web/public (original artwork).

    python3 brand_assets.py

og-image.png 1200x630 (Open Graph / Twitter), apple-touch-icon.png 180, icon-192.png,
icon-512.png and favicon-48.png (Google shows favicons from multiples of 48 px).
"""
from __future__ import annotations

import base64
import math
from pathlib import Path

from playwright.sync_api import sync_playwright

WEB = Path(__file__).resolve().parents[2] / "flaria-web"
PUBLIC = WEB / "public"
FONTS = WEB / "node_modules" / "@fontsource"

TRACE = "M8 36h9l4-12 6 22 6-30 6 26 4-6h13"


def icon_svg(size: int, rounded: bool) -> str:
    r = 14 if rounded else 0
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="{size}" height="{size}">'
            f'<rect width="64" height="64" rx="{r}" fill="#14525A"/>'
            f'<path d="{TRACE}" fill="none" stroke="#19A7B5" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/></svg>')


def face_and_wave() -> str:
    """Static rendition of the landing hero: a voice trace flowing from a face-mesh constellation."""
    parts = []
    cx, cy, fr = 905, 330, 150
    golden = math.pi * (3 - math.sqrt(5))
    for i in range(64):
        a = i / 64 * 2 * math.pi
        parts.append(f'<circle cx="{cx + math.cos(a) * 0.78 * fr:.1f}" cy="{cy + math.sin(a) * fr:.1f}" r="2.4" fill="#142028" opacity=".55"/>')
    for i in range(170):
        r = math.sqrt((i + .5) / 170) * .92
        a = i * golden
        x, y = math.cos(a) * r * .78, math.sin(a) * r
        if math.hypot((abs(x) - .3) / .2, (y + .22) / .1) < 1.2 or math.hypot(x / .3, (y - .5) / .12) < 1.2:
            continue
        parts.append(f'<circle cx="{cx + x * fr:.1f}" cy="{cy + y * fr:.1f}" r="2" fill="#4A5A66" opacity=".45"/>')
    for side in (-1, 1):
        for i in range(16):
            a = i / 16 * 2 * math.pi
            parts.append(f'<circle cx="{cx + (side * .3 + math.cos(a) * .15) * fr:.1f}" cy="{cy + (-.22 + math.sin(a) * .06) * fr:.1f}" r="3" fill="#19A7B5"/>')
    for i in range(24):
        a = i / 24 * 2 * math.pi
        parts.append(f'<circle cx="{cx + math.cos(a) * .24 * fr:.1f}" cy="{cy + (.5 + math.sin(a) * .09) * fr:.1f}" r="3" fill="#19A7B5"/>')
    pts = []
    end = cx - fr * .95
    for k in range(0, int(end - 560), 2):
        x = end - k
        t = k / 120
        env = max(0.0, math.sin(2 * math.pi * 3.1 * t)) ** .8 * (0.5 + 0.5 * math.tanh(3 * math.sin(2 * math.pi * .45 * t + .6)))
        y = cy + env * 62 * (math.sin(2 * math.pi * 16 * t) + .45 * math.sin(2 * math.pi * 32 * t + .8))
        pts.append(f"{x:.1f},{y:.1f}")
    parts.insert(0, '<defs><linearGradient id="g" x1="560" x2="760" gradientUnits="userSpaceOnUse">'
                    '<stop offset="0" stop-color="#1D6B73" stop-opacity="0"/><stop offset="1" stop-color="#1D6B73"/></linearGradient></defs>'
                    f'<polyline points="{" ".join(pts)}" fill="none" stroke="url(#g)" stroke-width="3" stroke-linejoin="round"/>')
    parts.append(f'<line x1="{cx - .24 * fr}" y1="{cy + .5 * fr}" x2="{cx + .24 * fr}" y2="{cy + .5 * fr}" stroke="#1D6B73" stroke-width="2.5"/>')
    return "".join(parts)


def og_html() -> str:
    def face(family: str, weight: int, file: str) -> str:
        # Embedded as data URIs: an in-memory page can't load file:// fonts.
        data = base64.b64encode((FONTS / file).read_bytes()).decode()
        return f"@font-face{{font-family:'{family}';font-weight:{weight};src:url(data:font/woff2;base64,{data}) format('woff2')}}"
    fonts = "".join([
        face("Plex Condensed", 600, "ibm-plex-sans-condensed/files/ibm-plex-sans-condensed-latin-600-normal.woff2"),
        face("Plex Condensed", 700, "ibm-plex-sans-condensed/files/ibm-plex-sans-condensed-latin-700-normal.woff2"),
        face("Plex", 400, "ibm-plex-sans/files/ibm-plex-sans-latin-400-normal.woff2"),
        face("Plex", 500, "ibm-plex-sans/files/ibm-plex-sans-latin-500-normal.woff2"),
    ])
    grid = ("background-color:#EDF1F2;background-image:linear-gradient(to right,rgba(29,107,115,.10) 1px,transparent 1px),"
            "linear-gradient(to bottom,rgba(29,107,115,.10) 1px,transparent 1px),linear-gradient(to right,rgba(29,107,115,.045) 1px,transparent 1px),"
            "linear-gradient(to bottom,rgba(29,107,115,.045) 1px,transparent 1px);background-size:80px 80px,80px 80px,16px 16px,16px 16px")
    return f"""<!doctype html><html><head><style>{fonts}
      *{{margin:0;box-sizing:border-box}} body{{width:1200px;height:630px;{grid};font-family:Plex,sans-serif;color:#142028;overflow:hidden}}
      .brand{{position:absolute;left:72px;top:64px;display:flex;align-items:center;gap:18px}}
      .brand span{{font-family:'Plex Condensed';font-weight:700;font-size:52px;letter-spacing:-.01em}}
      h1{{position:absolute;left:72px;top:190px;width:560px;font-family:'Plex Condensed';font-weight:600;font-size:64px;line-height:1.02;letter-spacing:-.015em}}
      p{{position:absolute;left:74px;top:470px;width:520px;font-size:24px;line-height:1.4;color:#4A5A66}}
      .art{{position:absolute;inset:0}} .rule{{position:absolute;left:0;right:0;bottom:0;height:10px;background:#14525A}}
    </style></head><body>
      <svg class="art" viewBox="0 0 1200 630" width="1200" height="630">{face_and_wave()}</svg>
      <div class="brand"><svg viewBox="0 0 40 24" width="74" height="44"><path d="M1 14h6l3-8 4 14 4-18 4 16 3-4h14" fill="none" stroke="#19A7B5" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg><span>Flaria</span></div>
      <h1>Spot early change in voice and facial movement</h1>
      <p>Compared with your own baseline. Video never leaves your device.</p>
      <div class="rule"></div>
    </body></html>"""


def main() -> None:
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1200, "height": 630})
        page.set_content(og_html())
        page.evaluate("document.fonts.ready.then(() => document.fonts.size)")
        page.wait_for_timeout(300)
        page.screenshot(path=str(PUBLIC / "og-image.png"))
        for name, size, rounded in (("apple-touch-icon.png", 180, False), ("icon-192.png", 192, True),
                                    ("icon-512.png", 512, True), ("favicon-48.png", 48, True)):
            p = browser.new_page(viewport={"width": size, "height": size})
            p.set_content(f"<html><body style='margin:0;background:transparent'>{icon_svg(size, rounded)}</body></html>")
            p.locator("svg").screenshot(path=str(PUBLIC / name), omit_background=True)
            p.close()
        browser.close()
    print("brand assets written to", PUBLIC)


if __name__ == "__main__":
    main()
