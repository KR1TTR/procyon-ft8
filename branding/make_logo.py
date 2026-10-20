"""Build Procyon's logo files from the app icon and the wordmark.

The lettering is converted to outlines by Inkscape, so the SVGs look the same
everywhere, with no fonts needed. Rebuilding needs Inkscape plus the fonts
Noto Sans Bold and DejaVu Sans Mono Bold.
    python3 branding/make_logo.py
"""
import os
import re
import subprocess
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ICON = os.path.join(HERE, "..", "procyon", "web", "icon.svg")

# Layout in units of 0.1 px of the app header: icon 260, gap 80, "Procyon"
# 170 bold, then the FT8 tag. Lettering offsets measured with Inkscape.
ICON_SIZE, GAP = 260, 80
WORD_W, WORD_LEFT, WORD_CAP = 648.55, 15.3, 121.4
TAG_TEXT_W, TAG_LEFT, TAG_CAP = 175.43, 8.89, 74.2
TAG_PAD, TAG_H, TAG_R = 50, 160, 40
MID = ICON_SIZE / 2

# Stacked (for apparel): the raccoon centred above the wordmark
STACK_ICON, STACK_GAP = 560, 70
ROW_TOP, ROW_BOTTOM = MID - TAG_H / 2, MID + WORD_CAP / 2 + 40.8   # tag top .. bottom of the "y"

COLOURS = {
    "dark": {"word": "#e7ecf6", "tag": "#3dd6c4", "tag_text": "#080c17"},    # as in the app
    "light": {"word": "#0b1020", "tag": "#3dd6c4", "tag_text": "#080c17"},
}


def icon_body(flat=False):
    """The icon's shapes (and its gradient), without the outer <svg>."""
    svg = open(ICON).read()
    body = re.search(r"<svg[^>]*>(.*)</svg>", svg, re.S).group(1)
    body = re.sub(r"<!--.*?-->", "", body, flags=re.S)
    if flat:   # embroidery: no soft glow behind the star eyes
        body = re.sub(r'<circle[^>]*url\(#glow\)[^>]*/>\s*', "", body)
        body = re.sub(r"<defs>.*?</defs>", "", body, flags=re.S)
    return body


def wordmark(x0, colours):
    word_x = x0 - WORD_LEFT
    tag_x = x0 + WORD_W + GAP
    tag_w = TAG_TEXT_W + 2 * TAG_PAD
    return f'''
  <text x="{word_x:.2f}" y="{MID + WORD_CAP / 2:.2f}" font-family="Noto Sans" font-weight="700"
        font-size="170" letter-spacing="-1.7" fill="{colours['word']}">Procyon</text>
  <rect x="{tag_x:.2f}" y="{MID - TAG_H / 2:.2f}" width="{tag_w:.2f}" height="{TAG_H}" rx="{TAG_R}" fill="{colours['tag']}"/>
  <text x="{tag_x + TAG_PAD - TAG_LEFT:.2f}" y="{MID + TAG_CAP / 2:.2f}" font-family="DejaVu Sans Mono"
        font-weight="700" font-size="100" letter-spacing="5" fill="{colours['tag_text']}">FT8</text>''', tag_x + tag_w


def outline(svg_text, out):
    """Write the SVG with its text turned into shapes."""
    with tempfile.NamedTemporaryFile("w", suffix=".svg", delete=False) as f:
        f.write(svg_text)
    subprocess.run(["inkscape", f.name, "--export-text-to-path", "--export-plain-svg",
                    f"--export-filename={out}"], check=True, capture_output=True)
    os.unlink(f.name)


def png(svg, out, width):
    subprocess.run(["inkscape", svg, f"--export-width={width}", f"--export-filename={out}"],
                   check=True, capture_output=True)


def main():
    made = []
    for bg, colours in COLOURS.items():
        for flat in (False, True):
            marks, right = wordmark(ICON_SIZE + GAP, colours)
            icon = f'<g transform="scale({ICON_SIZE / 32})">{icon_body(flat)}</g>'
            name = f"procyon-logo-{bg}-bg{'-flat' if flat else ''}"
            svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {right:.0f} {ICON_SIZE}">'
                   f"<title>Procyon FT8</title>{icon}{marks}\n</svg>\n")
            outline(svg, os.path.join(HERE, name + ".svg"))
            png(os.path.join(HERE, name + ".svg"), os.path.join(HERE, name + ".png"), 4000)
            made.append(name)
    # Stacked: raccoon on top, "Procyon FT8" underneath - best on caps and shirts
    for bg, colours in COLOURS.items():
        for flat in (False, True):
            marks, right = wordmark(0, colours)
            dy = STACK_ICON + STACK_GAP - ROW_TOP
            icon = (f'<g transform="translate({(right - STACK_ICON) / 2:.2f} 0) scale({STACK_ICON / 32})">'
                    f"{icon_body(flat)}</g>")
            name = f"procyon-logo-stacked-{bg}-bg{'-flat' if flat else ''}"
            svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {right:.0f} {dy + ROW_BOTTOM:.0f}">'
                   f'<title>Procyon FT8</title>{icon}<g transform="translate(0 {dy:.2f})">{marks}</g>\n</svg>\n')
            outline(svg, os.path.join(HERE, name + ".svg"))
            png(os.path.join(HERE, name + ".svg"), os.path.join(HERE, name + ".png"), 3000)
            made.append(name)
    # The icon on its own, large, for a hat side or a sticker
    for flat in (False, True):
        name = f"procyon-icon{'-flat' if flat else ''}"
        with open(os.path.join(HERE, name + ".svg"), "w") as f:
            f.write(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
                    f"<title>Procyon</title>{icon_body(flat)}</svg>\n")
        png(os.path.join(HERE, name + ".svg"), os.path.join(HERE, name + ".png"), 2000)
        made.append(name)
    # Wordmark for the app header (the icon is shown separately there)
    marks, right = wordmark(0, COLOURS["dark"])
    outline(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {right:.0f} {ICON_SIZE}">'
            f"<title>Procyon FT8</title>{marks}\n</svg>\n",
            os.path.join(HERE, "..", "procyon", "web", "wordmark.svg"))
    made.append("procyon/web/wordmark.svg")
    print("made:", ", ".join(made))


if __name__ == "__main__":
    main()
