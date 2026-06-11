#!/usr/bin/env python3
"""Build menu bar busy (transcribing) icon: V + waveform stencil.

Same visual family as menubar_idle (chunky V with a glyph tucked into the
lower-right), but the glyph is a 5-bar audio waveform instead of a
microphone. The V's geometry is matched to menubar_idle so the menu bar
icon does not change size when state flips. The transparent gap between
the V and the waveform follows each bar's silhouette (a thin uniform
outline traced around every bar), the same technique preview_icons.py
uses for the mic halo. Output is a transparent-background stencil
(black RGB + alpha) so macOS tints it for light/dark menu bars.
"""
import os
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "assets")

BASE = 540  # supersampled canvas, downscaled with LANCZOS for each menu bar size
OUTPUT_SIZES = [
    (18, "menubar_busy.png"),
    (36, "menubar_busy@2x.png"),
    (54, "menubar_busy@3x.png"),
]

# V geometry — measured from menubar_idle@3x so the busy V matches it.
# (Idle V tops at row 9/54 ≈ 0.17, apex at row 48/54 ≈ 0.89, outer width
# from col 7-47, inner gap from col 18-36, inner apex around row 27.)
V_OUTER_TOP_L = (0.13, 0.17)
V_OUTER_TOP_R = (0.87, 0.17)
V_OUTER_APEX  = (0.50, 0.89)
V_INNER_TOP_L = (0.33, 0.17)
V_INNER_TOP_R = (0.67, 0.17)
V_INNER_APEX  = (0.50, 0.50)

# Waveform position (lower-right, mirrors idle's mic placement) and shape.
WAVE_CENTER = (0.75, 0.65)
WAVE_TOTAL_W = 0.42           # total horizontal extent
WAVE_BAR_W = 0.062            # each bar's thickness
WAVE_HEIGHTS = [0.14, 0.24, 0.36, 0.24, 0.14]  # symmetric — center bar tallest

# Thickness of the transparent silhouette gap between the V and each bar.
# Uniform pad applied on every side of each bar, so the cleared area
# follows the bar shape — thin frame, no fat halo.
HALO_PAD = 0.035


def _draw_v(draw, s):
    """Filled chunky V matching menubar_idle proportions."""
    p = lambda fxy: (fxy[0] * s, fxy[1] * s)
    draw.polygon(
        [p(V_OUTER_TOP_L), p(V_OUTER_APEX), p(V_OUTER_TOP_R),
         p(V_INNER_TOP_R), p(V_INNER_APEX), p(V_INNER_TOP_L)],
        fill=(0, 0, 0, 255),
    )


def _bar_geometry(s):
    """Yield (cx, top_y, bot_y, half_w) for each waveform bar."""
    cx_wave = WAVE_CENTER[0] * s
    cy_wave = WAVE_CENTER[1] * s
    total_w = WAVE_TOTAL_W * s
    bar_w = WAVE_BAR_W * s
    half_w = bar_w / 2.0
    n = len(WAVE_HEIGHTS)
    gap = (total_w - n * bar_w) / (n - 1)
    x_left = cx_wave - total_w / 2.0
    for i, hf in enumerate(WAVE_HEIGHTS):
        cx = x_left + bar_w / 2.0 + i * (bar_w + gap)
        h = hf * s
        yield cx, cy_wave - h / 2.0, cy_wave + h / 2.0, half_w


def _draw_rounded_bar(draw, cx, top_y, bot_y, half_w):
    """Vertical bar with rounded (semicircular) ends."""
    left = cx - half_w
    right = cx + half_w
    draw.rectangle([left, top_y + half_w, right, bot_y - half_w], fill=255)
    draw.ellipse([left, top_y, right, top_y + 2 * half_w], fill=255)
    draw.ellipse([left, bot_y - 2 * half_w, right, bot_y], fill=255)


def _draw_waveform_with_halo(img, s):
    """Stamp the 5 bars, after carving a thin per-bar silhouette out of the V."""
    halo_pad = HALO_PAD * s

    # Halo mask: each bar grown uniformly by halo_pad on every side.
    # Where this is opaque, the V's alpha is erased — so the cleared
    # region follows the bar shape, leaving a thin uniform outline.
    halo = Image.new("L", img.size, 0)
    halo_draw = ImageDraw.Draw(halo)
    for cx, top_y, bot_y, half_w in _bar_geometry(s):
        _draw_rounded_bar(
            halo_draw, cx, top_y - halo_pad, bot_y + halo_pad, half_w + halo_pad,
        )

    a = img.split()[-1]
    a_arr = a.load()
    h_arr = halo.load()
    w, hgt = img.size
    for yy in range(hgt):
        for xx in range(w):
            if h_arr[xx, yy]:
                a_arr[xx, yy] = 0
    img.putalpha(a)

    # Now stamp the real bars on top, fully opaque.
    bar_layer = Image.new("L", img.size, 0)
    bar_draw = ImageDraw.Draw(bar_layer)
    for cx, top_y, bot_y, half_w in _bar_geometry(s):
        _draw_rounded_bar(bar_draw, cx, top_y, bot_y, half_w)
    black_rgb = Image.new("RGBA", img.size, (0, 0, 0, 0))
    black_rgb.putalpha(bar_layer)
    img.alpha_composite(black_rgb)


def _build_master():
    img = Image.new("RGBA", (BASE, BASE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    _draw_v(draw, BASE)
    _draw_waveform_with_halo(img, BASE)
    return img


def main():
    if not os.path.isdir(OUT_DIR):
        os.makedirs(OUT_DIR)
    master = _build_master()
    for px_size, name in OUTPUT_SIZES:
        out_path = os.path.join(OUT_DIR, name)
        master.resize((px_size, px_size), Image.LANCZOS).save(out_path)
        print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
