"""Build Procyon's business cards: the stacked tagline logo on the left, a QR code
to the website on the right. Dark and light versions.

US card size 3.5 x 2 in, with a 1/8 in bleed on every side (3.75 x 2.25 in),
as printers ask for. Needs what make_logo.py needs, plus the Python "qrcode"
module.
    python3 branding/make_cards.py
"""
import os

import qrcode

import make_logo as L

URL = "https://procyon.kr1ttr.radio"
URL_TEXT = "procyon.kr1ttr.radio"

# Units: 1/1000 inch. The cut line is 125 in from every edge of the file;
# everything that matters stays a further 1/8 in inside it.
W, H, BLEED = 3750, 2250, 125
SAFE = BLEED + 125

CARD = {
    "dark": {"bg": "#0b1020", "url": "#8e9ab3", "qr": "#0b1020", "tile": "#ffffff"},
    "light": {"bg": "#ffffff", "url": "#5a6478", "qr": "#0b1020", "tile": None},
}


def qr_path(size):
    """The QR code as one path, `size` wide, plus its module count."""
    q = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=0)
    q.add_data(URL)
    q.make(fit=True)
    m = q.get_matrix()
    n = len(m)
    u = size / n
    d = "".join(f"M{x * u:.2f} {y * u:.2f}h{u:.2f}v{u:.2f}h-{u:.2f}z"
                for y, row in enumerate(m) for x, on in enumerate(row) if on)
    return d, n


def card(bg):
    c, colours = CARD[bg], L.COLOURS[bg]
    # Left: the stacked tagline logo (as on the stickers), so the raccoon can be big
    marks, right = L.with_tagline(colours)
    top = L.MID - L.WORD_CAP / 2
    icon = 420
    dy = icon + 60 - top
    logo_h = dy + L.LINE_Y + 12
    scale = 1450 / logo_h                      # about 1.45 in tall
    logo_w = right * scale
    logo_x = SAFE + 60 + (1900 - 60 - logo_w) / 2   # centred in the left part of the card
    logo_y = H / 2 - logo_h * scale / 2
    logo = (f'<g transform="translate({logo_x:.1f} {logo_y:.1f}) scale({scale:.4f})">'
            f'<g transform="translate({(right - icon) / 2:.2f} 0) scale({icon / 32})">{L.icon_body()}</g>'
            f'<g transform="translate(0 {dy:.2f})">{marks}</g></g>')
    # Right: the QR code (dark on light: scanners read that best), web address under it
    qr_size, quiet = 800, 80
    tile = qr_size + 2 * quiet
    tx = W - SAFE - 80 - tile
    ty = H / 2 - (tile + 190) / 2
    d, _ = qr_path(qr_size)
    tile_rect = (f'<rect x="{tx:.1f}" y="{ty:.1f}" width="{tile}" height="{tile}" rx="40" fill="{c["tile"]}"/>'
                 if c["tile"] else "")
    qr = (f'{tile_rect}<path transform="translate({tx + quiet:.1f} {ty + quiet:.1f})" d="{d}" '
          f'fill="{c["qr"]}"/>'
          f'<text x="{tx + tile / 2:.1f}" y="{ty + tile + 150:.1f}" text-anchor="middle" font-family="Noto Sans" '
          f'font-size="78" fill="{c["url"]}">{URL_TEXT}</text>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="3.75in" height="2.25in" viewBox="0 0 {W} {H}">'
            f'<title>Procyon business card</title><rect width="{W}" height="{H}" fill="{c["bg"]}"/>'
            f"{logo}{qr}\n</svg>\n")


def main():
    made = []
    for bg in CARD:
        name = f"procyon-card-{bg}"
        L.outline(card(bg), os.path.join(L.HERE, name + ".svg"))
        L.png(os.path.join(L.HERE, name + ".svg"), os.path.join(L.HERE, name + ".png"), 1125)   # 300 dpi
        made.append(name)
    print("made:", ", ".join(made))


if __name__ == "__main__":
    main()
