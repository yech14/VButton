#!/usr/bin/env python3
"""Build VButton.icns and the menu-bar template PNGs from one source image.

Source: design_refs/ref_v_design_final.png — a sheet containing a Light Mode
tile and a Dark Mode tile. The Dark Mode tile is the canonical artwork.

App icon:
    Crop the Dark Mode tile and resize for every macOS iconset slot.

Menu bar idle icon:
    Crop the same tile, then convert pixel luminance to alpha so the V+mic
    glyph becomes a transparent-background stencil. macOS will tint it for
    light/dark menu bars when the NSImage is marked as a template.
"""
import os
import shutil
import subprocess

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ICONSET = os.path.join(HERE, "VButton.iconset")
ICNS = os.path.join(HERE, "VButton.icns")
APP_BUNDLE_ICNS = os.path.join(HERE, "VButton.app", "Contents", "Resources", "VButton.icns")
REF_APP_ICON = os.path.join(HERE, "design_refs", "ref_v_design_final.png")
MENUBAR_DIR = os.path.join(HERE, "assets")

BG_LUMA_THRESHOLD = 80  # pixel luma below this is treated as "dark tile bg"


ICONSET_SIZES = [
    (16, "icon_16x16.png"),
    (32, "icon_16x16@2x.png"),
    (32, "icon_32x32.png"),
    (64, "icon_32x32@2x.png"),
    (128, "icon_128x128.png"),
    (256, "icon_128x128@2x.png"),
    (256, "icon_256x256.png"),
    (512, "icon_256x256@2x.png"),
    (512, "icon_512x512.png"),
    (1024, "icon_512x512@2x.png"),
]

MENUBAR_POINT_SIZE = 18  # logical pt size of the menu bar icon
MENUBAR_SIZES = [
    (18, "menubar_idle.png"),     # @1x
    (36, "menubar_idle@2x.png"),  # @2x — Retina
    (54, "menubar_idle@3x.png"),  # @3x
]


def _find_dark_tile_bbox(im):
    """Locate the Dark Mode tile (the right tile in the reference sheet)."""
    w, h = im.size
    px = im.load()
    candidate_ys = [int(h * f) for f in (0.18, 0.22, 0.26, 0.30, 0.34)]
    best = None  # (length, x0, x1)
    for y in candidate_ys:
        in_dark = False
        start = None
        for x in range(int(w * 0.45), w):
            r, g, b = px[x, y][:3]
            luma = (r + g + b) // 3
            is_dark = luma < BG_LUMA_THRESHOLD
            if is_dark and not in_dark:
                start = x
                in_dark = True
            elif not is_dark and in_dark:
                length = x - start
                if best is None or length > best[0]:
                    best = (length, start, x - 1)
                in_dark = False
        if in_dark:
            length = w - start
            if best is None or length > best[0]:
                best = (length, start, w - 1)
    if best is None:
        raise RuntimeError(f"could not locate dark tile in {REF_APP_ICON}")
    _, x0, x1 = best
    x_probe = (x0 + x1) // 2
    y0 = None
    y1 = None
    for y in range(h):
        r, g, b = px[x_probe, y][:3]
        if (r + g + b) // 3 < BG_LUMA_THRESHOLD:
            if y0 is None:
                y0 = y
            y1 = y
    return (x0, y0, x1 + 1, y1 + 1)


def _square_pad(tile, bg_color):
    tw, th = tile.size
    side = max(tw, th)
    sq = Image.new("RGB", (side, side), bg_color) if tile.mode == "RGB" \
        else Image.new(tile.mode, (side, side), bg_color)
    sq.paste(tile, ((side - tw) // 2, (side - th) // 2))
    return sq


# ---------- App icon: resize the dark tile for every iconset slot ----------

def _build_iconset(tile):
    bg = tile.getpixel((6, 6))
    sq = _square_pad(tile, bg)

    if os.path.exists(ICONSET):
        shutil.rmtree(ICONSET)
    os.makedirs(ICONSET)
    for px_size, name in ICONSET_SIZES:
        sq.resize((px_size, px_size), Image.LANCZOS).save(
            os.path.join(ICONSET, name)
        )


def _build_icns():
    subprocess.run(["iconutil", "-c", "icns", ICONSET, "-o", ICNS], check=True)
    shutil.rmtree(ICONSET)
    if os.path.isdir(os.path.dirname(APP_BUNDLE_ICNS)):
        shutil.copy2(ICNS, APP_BUNDLE_ICNS)


# ---------- Menu bar idle: tile → alpha stencil → template PNG ----------

def _tile_to_alpha_stencil(tile):
    """Convert the dark tile into a transparent-bg stencil.

    The Dark Mode tile has a near-black/purple background and a white V+mic
    glyph. We compute each pixel's luminance, subtract the background
    luminance, and use the result as the alpha channel. The RGB channel is
    set to black because macOS replaces it when the NSImage is a template.
    """
    rgb = tile.convert("RGB")
    w, h = rgb.size

    # Sample bg luma from a few corner pixels (more robust than one).
    samples = [
        rgb.getpixel((4, 4)),
        rgb.getpixel((w - 5, 4)),
        rgb.getpixel((4, h - 5)),
        rgb.getpixel((w - 5, h - 5)),
    ]
    bg_lumas = [(r + g + b) / 3.0 for (r, g, b) in samples]
    bg_luma = sum(bg_lumas) / len(bg_lumas)

    # Compute a luminance image, normalize against bg_luma → alpha map.
    gray = rgb.convert("L")
    gpx = gray.load()
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    opx = out.load()
    denom = max(1.0, 255.0 - bg_luma)
    for y in range(h):
        for x in range(w):
            luma = gpx[x, y]
            if luma <= bg_luma:
                a = 0
            else:
                a = int(round((luma - bg_luma) / denom * 255))
                if a > 255:
                    a = 255
            opx[x, y] = (0, 0, 0, a)
    return out


def _build_menubar_assets(tile):
    stencil = _tile_to_alpha_stencil(tile)
    # Square-pad in RGBA with transparent fill so the resize stays centred.
    tw, th = stencil.size
    side = max(tw, th)
    sq = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    sq.paste(stencil, ((side - tw) // 2, (side - th) // 2))

    if not os.path.exists(MENUBAR_DIR):
        os.makedirs(MENUBAR_DIR)
    for px_size, name in MENUBAR_SIZES:
        sq.resize((px_size, px_size), Image.LANCZOS).save(
            os.path.join(MENUBAR_DIR, name)
        )


def main():
    if not os.path.isfile(REF_APP_ICON):
        raise SystemExit(f"reference image not found: {REF_APP_ICON}")

    im = Image.open(REF_APP_ICON).convert("RGB")
    bbox = _find_dark_tile_bbox(im)
    tile = im.crop(bbox)

    _build_iconset(tile)
    _build_icns()
    print(f"wrote {ICNS}")
    if os.path.exists(APP_BUNDLE_ICNS):
        print(f"updated bundle {APP_BUNDLE_ICNS}")

    _build_menubar_assets(tile)
    print(f"wrote menu bar assets in {MENUBAR_DIR}")


if __name__ == "__main__":
    main()
