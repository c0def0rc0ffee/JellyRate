#!/usr/bin/env python3
"""
<summary>
Draws the README artwork for JellyRate: docs/screenshots/banner.png (1280 by 320)
and docs/screenshots/social.png (1280 by 640, for the GitHub social preview).
Both use one flat navy background, the name and tagline on the left and three
rounded tiles showing what the add-on really shows: a ten star row, the
"Previously rated" line and a short ratings list.
</summary>
<remarks>
Development tool only. It needs Pillow, which the add-on itself never imports,
and it is not part of the Dist zip. Everything is drawn at twice the size and
scaled down so the star edges are smooth. The list titles are invented for the
picture and are not real films. Fonts are read from the system font folders;
the script stops with a message if neither Noto Sans nor DejaVu Sans is found.
Run from anywhere: python3 tools/make_banner.py
</remarks>
"""
import os
import sys

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, 'docs', 'screenshots')
SCALE = 2

BACKGROUND = (0x0d, 0x16, 0x2b)
PANEL = (0x07, 0x0e, 0x1d)
NAME_COLOUR = (0xf0, 0xf4, 0xfa)
TAGLINE_COLOUR = (0x94, 0xa3, 0xb8)
DIM = (0x33, 0x41, 0x55)
GOLD = (0xf5, 0xb3, 0x01)
TEAL = (0x2d, 0xd4, 0xbf)
VIOLET = (0x8b, 0x5c, 0xf6)

NAME = 'JellyRate'
TAGLINE = 'Rate what you just watched. Saved straight to Jellyfin.'
# Invented titles, made up for the picture only.
SAMPLE_LIST = (('Teacup Orbit', '9/10'), ('Moss Patrol', '7/10'), ('Lighthouse Pie', None))

FONT_DIRS = ('/usr/share/fonts/truetype/noto', '/usr/share/fonts/truetype/dejavu')
BOLD_NAMES = ('NotoSans-Bold.ttf', 'DejaVuSans-Bold.ttf')
REGULAR_NAMES = ('NotoSans-Regular.ttf', 'DejaVuSans.ttf')


def find_font(names):
    """
    <summary>
    Return the path of the first font file found from a preference list.
    </summary>
    <param name="names">File names in order of preference.</param>
    <returns>An absolute path to a TrueType file.</returns>
    <exception cref="SystemExit">When none of the files exists on this machine.</exception>
    """
    for name in names:
        for folder in FONT_DIRS:
            path = os.path.join(folder, name)
            if os.path.isfile(path):
                return path
    sys.exit('No usable font found, install fonts-noto-core or fonts-dejavu-core')


BOLD = find_font(BOLD_NAMES)
REGULAR = find_font(REGULAR_NAMES)


def font(path, size):
    """
    <summary>Load a font at a size given in final pixels, scaled for the double size canvas.</summary>
    <param name="path">Font file.</param>
    <param name="size">Size in final image pixels.</param>
    <returns>A Pillow FreeType font.</returns>
    """
    return ImageFont.truetype(path, int(size * SCALE))


def star_points(cx, cy, outer):
    """
    <summary>
    The ten corner points of a five pointed star centred on a point, top point up.
    </summary>
    <param name="cx">Centre x on the canvas.</param>
    <param name="cy">Centre y on the canvas.</param>
    <param name="outer">Radius to the tips; the inner corners sit at 0.42 of it.</param>
    <returns>A list of (x, y) tuples for ImageDraw.polygon.</returns>
    """
    import math
    points = []
    for i in range(10):
        radius = outer if i % 2 == 0 else outer * 0.42
        angle = math.pi / 2 + i * math.pi / 5
        points.append((cx + radius * math.cos(angle), cy - radius * math.sin(angle)))
    return points


def panel(draw, x, y, size, accent):
    """
    <summary>Draw one rounded tile: a dark panel with a thin border in its accent colour.</summary>
    <param name="draw">ImageDraw on the double size canvas.</param>
    <param name="x">Left edge in final pixels.</param>
    <param name="y">Top edge in final pixels.</param>
    <param name="size">Side length in final pixels.</param>
    <param name="accent">Border colour.</param>
    """
    s = SCALE
    draw.rounded_rectangle((x * s, y * s, (x + size) * s, (y + size) * s),
                           radius=int(size * 0.12 * s), fill=PANEL,
                           outline=accent, width=max(2, int(size * 0.012 * s)))


def tile_stars(draw, x, y, size):
    """
    <summary>
    Gold tile: the ten stars of the rating dialog in two rows of five, eight filled.
    </summary>
    <param name="draw">ImageDraw on the double size canvas.</param>
    <param name="x">Left edge in final pixels.</param>
    <param name="y">Top edge in final pixels.</param>
    <param name="size">Side length in final pixels.</param>
    """
    panel(draw, x, y, size, GOLD)
    s = SCALE
    step = size * 0.17
    radius = size * 0.075
    left = x + size / 2 - 2 * step
    for row in range(2):
        cy = y + size * (0.32 + row * 0.24)
        for col in range(5):
            number = row * 5 + col + 1
            pts = [(px * s, py * s) for px, py in star_points(left + col * step, cy, radius)]
            if number <= 8:
                draw.polygon(pts, fill=GOLD)
            else:
                draw.polygon(pts, outline=GOLD, width=max(2, int(size * 0.012 * s)))
    label = font(BOLD, size * 0.1)
    draw.text(((x + size / 2) * s, (y + size * 0.8) * s), '8 of 10', font=label,
              fill=GOLD, anchor='mm')


def tile_previous(draw, x, y, size):
    """
    <summary>Teal tile: the "Previously rated" line the dialog shows for a title rated before.</summary>
    <param name="draw">ImageDraw on the double size canvas.</param>
    <param name="x">Left edge in final pixels.</param>
    <param name="y">Top edge in final pixels.</param>
    <param name="size">Side length in final pixels.</param>
    """
    panel(draw, x, y, size, TEAL)
    s = SCALE
    small = font(REGULAR, size * 0.1)
    big = font(BOLD, size * 0.32)
    cx = (x + size / 2) * s
    draw.text((cx, (y + size * 0.3) * s), 'Previously rated:', font=small,
              fill=TAGLINE_COLOUR, anchor='mm')
    draw.text((cx, (y + size * 0.6) * s), '8/10', font=big, fill=TEAL, anchor='mm')


def tile_list(draw, x, y, size):
    """
    <summary>
    Violet tile: three rows of the My Ratings list, two rated and one not yet rated.
    </summary>
    <param name="draw">ImageDraw on the double size canvas.</param>
    <param name="x">Left edge in final pixels.</param>
    <param name="y">Top edge in final pixels.</param>
    <param name="size">Side length in final pixels.</param>
    <remarks>The titles come from SAMPLE_LIST and are invented.</remarks>
    """
    panel(draw, x, y, size, VIOLET)
    s = SCALE
    text = font(REGULAR, size * 0.085)
    score = font(BOLD, size * 0.085)
    pad = size * 0.1
    row_h = size * 0.24
    for i, (title, value) in enumerate(SAMPLE_LIST):
        top = y + size * 0.16 + i * row_h
        draw.rounded_rectangle(((x + pad * 0.6) * s, top * s, (x + size - pad * 0.6) * s,
                                (top + row_h * 0.8) * s), radius=int(size * 0.04 * s),
                               fill=(0x14, 0x1c, 0x33) if i == 0 else PANEL)
        mid = (top + row_h * 0.4) * s
        draw.text(((x + pad) * s, mid), title, font=text, fill=NAME_COLOUR, anchor='lm')
        if value:
            draw.text(((x + size - pad) * s, mid), value, font=score, fill=VIOLET, anchor='rm')
        else:
            pts = [(px * s, py * s) for px, py in
                   star_points(x + size - pad - size * 0.05, top + row_h * 0.4, size * 0.05)]
            draw.polygon(pts, outline=DIM, width=max(2, int(size * 0.01 * s)))


def fitted_font(draw, path, text, size, max_width):
    """
    <summary>Shrink a font a pixel at a time until the text fits a width.</summary>
    <param name="draw">ImageDraw used for measuring.</param>
    <param name="path">Font file.</param>
    <param name="text">Text to fit.</param>
    <param name="size">Starting size in final pixels.</param>
    <param name="max_width">Widest allowed, in final pixels.</param>
    <returns>The font and the size chosen, in final pixels.</returns>
    """
    while size > 12:
        candidate = font(path, size)
        left, _, right, _ = draw.textbbox((0, 0), text, font=candidate)
        if (right - left) / SCALE <= max_width:
            return candidate, size
        size -= 1
    return font(path, size), size


def render(width, height, name_size, tagline_size, tile_size, tiles_left, tiles_top, text_width):
    """
    <summary>
    Draw one picture: background, name and tagline left aligned 80 pixels in,
    the text block centred vertically in the band above tiles_top or the whole
    height, and the three tiles in a row.
    </summary>
    <param name="width">Final width.</param>
    <param name="height">Final height.</param>
    <param name="name_size">Name font size.</param>
    <param name="tagline_size">Tagline font size before any shrink to fit.</param>
    <param name="tile_size">Tile side length.</param>
    <param name="tiles_left">Left edge of the first tile.</param>
    <param name="tiles_top">Top edge of the tiles; tiles centred vertically when None.</param>
    <param name="text_width">Widest the tagline may run.</param>
    <returns>The finished image at final size.</returns>
    """
    s = SCALE
    image = Image.new('RGB', (width * s, height * s), BACKGROUND)
    draw = ImageDraw.Draw(image)
    name_font = font(BOLD, name_size)
    tag_font, tag_size = fitted_font(draw, REGULAR, TAGLINE, tagline_size, text_width)
    gap = name_size * 0.18
    block = name_size + gap + tag_size
    band = height if tiles_top is None else tiles_top
    top = (band - block) / 2 if tiles_top is None else (band - block) / 2 + 10
    draw.text((80 * s, top * s), NAME, font=name_font, fill=NAME_COLOUR, anchor='lt')
    draw.text((80 * s, (top + name_size + gap) * s), TAGLINE, font=tag_font,
              fill=TAGLINE_COLOUR, anchor='lt')
    ty = (height - tile_size) / 2 if tiles_top is None else tiles_top
    spacing = tile_size * 0.12
    for i, painter in enumerate((tile_stars, tile_previous, tile_list)):
        painter(draw, tiles_left + i * (tile_size + spacing), ty, tile_size)
    return image.resize((width, height), Image.LANCZOS)


def main():
    """
    <summary>Write banner.png and social.png into docs/screenshots, creating the folder if needed.</summary>
    <remarks>Saved without metadata chunks and with palette optimisation off so colours stay exact.</remarks>
    """
    os.makedirs(OUT_DIR, exist_ok=True)
    tile = 168
    tiles_width = 3 * tile + 2 * tile * 0.12
    banner = render(1280, 320, 92, 30, tile, 1280 - 64 - tiles_width, None,
                    1280 - 64 - tiles_width - 80 - 40)
    banner.save(os.path.join(OUT_DIR, 'banner.png'), optimize=True)
    social = render(1280, 640, 132, 40, 220, 80, 340, 1120)
    social.save(os.path.join(OUT_DIR, 'social.png'), optimize=True)
    print('Wrote banner.png and social.png in docs/screenshots')


if __name__ == '__main__':
    main()
