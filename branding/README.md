# Procyon logo files

These are vector logos with the lettering already turned into shapes, so they
look the same everywhere and need no fonts. Send a printer or embroiderer the
**SVG**; if they ask for a picture instead, send the **PNG** (3000-4000 pixels wide,
transparent background).

| File | Use it for |
|---|---|
| `procyon-logo-stacked-dark-bg-flat.svg` / `.png` | **Apparel: caps and shirts.** Raccoon above "Procyon FT8", for dark fabric, made for embroidery. |
| `procyon-logo-stacked-dark-bg.svg` / `.png` | Stacked, for printing (shirts, stickers) on dark fabric. |
| `procyon-logo-stacked-light-bg…` | Stacked, for light fabric: dark lettering. |
| `procyon-logo-dark-bg.svg` / `.png` | The long logo, as in the app, for dark backgrounds (banners, web). |
| `procyon-logo-light-bg.svg` / `.png` | Light fabric (white, grey, tan): dark lettering. |
| `…-flat.svg` / `.png` | Embroidery: same logo without the soft glow behind the star eyes (thread can't do gradients). |
| `procyon-icon.svg` / `.png`, `procyon-icon-flat…` | The raccoon on its own: hat side, sleeve, sticker, patch. |

## Colours

| Part | Hex |
|---|---|
| Lettering on dark fabric | `#E7ECF6` (near-white) |
| Lettering on light fabric | `#0B1020` (near-black) |
| FT8 tag | `#3DD6C4` (teal) with `#080C17` lettering |
| Raccoon head | `#C9D3E6`, ears `#AAB6CC`, inner ears `#141B2E`, forehead `#7F8BA3` |
| Mask and nose | `#0B1020` |
| Muzzle | `#EEF2F8` |
| Star eyes | `#FFF4C2` |

## Fonts (already outlined in the files)

"Procyon": Noto Sans Bold. "FT8": DejaVu Sans Mono Bold. Both free fonts.

## Embroidery tips

- Use the **stacked** logo on apparel. A cap front is about 2 inches tall;
  the stacked logo fills it and keeps "FT8" readable. On a polo's left chest,
  about 3.5 inches wide works for the stacked logo.
- Ask the embroiderer to treat the star eyes as small satin-stitch stars in
  pale yellow; the tiny forehead stripe can be left out if it's too small.
- Dark fabric shows the raccoon best: on white, its light-grey head blends in.

To rebuild after changing the icon: `python3 branding/make_logo.py`
(needs Inkscape and the two fonts).
