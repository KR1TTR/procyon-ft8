# Procyon logo files

These are vector logos with the lettering already turned into shapes, so they
look the same everywhere and need no fonts. Send a printer the **SVG**; if they
ask for a picture instead, send the **PNG** (3000-4000 pixels wide,
transparent background).

| File | Use it for |
|---|---|
| `procyon-card-dark.svg` / `.png`, `procyon-card-light…` | **Business cards**, ready to print: stacked logo on the left, a QR code to procyon.kr1ttr.radio on the right. |
| `procyon-logo-stacked-tagline-dark-bg.svg` / `.png` | **Stickers.** Raccoon above "Procyon" and "Friendly digital ham software", for a dark sticker. |
| `procyon-logo-stacked-tagline-light-bg…` | The same for a white or light sticker: dark lettering, deeper teal. |
| `procyon-logo-tagline-dark-bg.svg` / `.png` | **Cards, banners, the website.** Raccoon beside "Procyon" with the tagline underneath. |
| `procyon-logo-tagline-light-bg…` | The same for a white or light card. |
| `procyon-logo-dark-bg.svg` / `.png` | The plain logo as in the app (no tagline), for dark backgrounds. |
| `procyon-logo-light-bg.svg` / `.png` | The plain logo for light backgrounds. |
| `procyon-logo-stacked-…` (no "tagline") | Plain stacked logo: raccoon above "Procyon". |
| `…-flat.svg` / `.png` | Without the soft glow behind the star eyes (for embroidery, or printers that can't do gradients). |
| `procyon-icon.svg` / `.png`, `procyon-icon-flat…` | The raccoon on its own: a small round sticker, an avatar. |

## Colours

| Part | Hex |
|---|---|
| Lettering on dark | `#E7ECF6` (near-white) |
| Lettering on light | `#0B1020` (near-black) |
| Tagline | `#3DD6C4` (teal) on dark, `#0A8F84` (deeper teal) on light |
| Raccoon head | `#C9D3E6`, ears `#AAB6CC`, inner ears `#141B2E`, forehead `#7F8BA3` |
| Mask and nose | `#0B1020` |
| Muzzle | `#EEF2F8` |
| Star eyes | `#FFF4C2` |
| Dark background used in the previews | `#0B1020` |

## Fonts (already outlined in the files)

"Procyon": Noto Sans Bold. Tagline: DejaVu Sans Mono Bold, stretched to the
width of "Procyon". Both free fonts.

## Printing tips

- The tagline is about a fifth the height of "Procyon". Keep stickers at least
  about 2.5 inches wide so it stays easy to read.
- Dark stickers show the raccoon best: on white, its light-grey head blends in
  a little. Ask for a thin white border (die-cut) on dark stickers.
- Business cards: `procyon-card-…` are a standard US 3.5 x 2 inch card with
  the 1/8 inch bleed printers ask for already added (the file is 3.75 x 2.25
  inches; the PNG is 300 dpi). Everything important sits well inside the cut
  line. The QR code prints about 0.8 inch square. Scan a test print with a
  phone before ordering a box. The dark card puts the QR code on a white
  tile, since some phones can't read light-on-dark codes.

To rebuild after changing the icon or the tagline: `python3 branding/make_logo.py`
(needs Inkscape and the two fonts); the cards: `python3 branding/make_cards.py`
(also needs the Python `qrcode` module). The tagline text is `TAGLINE` at the top
of the script.
