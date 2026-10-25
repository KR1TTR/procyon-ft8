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

# The short tag after "Procyon" (it said "FT8" until 0.9.23). None = no tag;
# a new descriptor that fits where Procyon is heading may go here later.
TAG = None

# Layout in units of 0.1 px of the app header: icon 260, gap 80, "Procyon"
# 170 bold, then the tag (if any). Lettering offsets measured with Inkscape.
ICON_SIZE, GAP = 260, 80
WORD_W, WORD_LEFT, WORD_CAP = 648.55, 15.3, 121.4
TAG_TEXT_W, TAG_LEFT, TAG_CAP = 175.43, 8.89, 74.2
TAG_PAD, TAG_H, TAG_R = 50, 160, 40
MID = ICON_SIZE / 2

# Stacked (for apparel): the raccoon centred above the wordmark
STACK_ICON, STACK_GAP = 560, 70
ROW_TOP = MID - TAG_H / 2 if TAG else MID - WORD_CAP / 2        # top of the tag, else of the capital P
ROW_BOTTOM = MID + WORD_CAP / 2 + 40.8                            # .. bottom of the "y"

COLOURS = {
    "dark": {"word": "#e7ecf6", "tag": "#3dd6c4", "tag_text": "#080c17", "line": "#3dd6c4"},  # as in the app
    "light": {"word": "#0b1020", "tag": "#3dd6c4", "tag_text": "#080c17", "line": "#0a8f84"},
}

# The tagline under "Procyon" on stickers and cards (since 0.9.40): teal
# capitals in the mono font, stretched to the width of "Procyon".
TAGLINE = "FRIENDLY DIGITAL HAM SOFTWARE"
LINE_SIZE, LINE_GAP = 38, 30
LINE_Y = MID + WORD_CAP / 2 + 40.8 + LINE_GAP + LINE_SIZE * 0.75   # baseline, below the "y"


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
    word = f'''
  <text x="{word_x:.2f}" y="{MID + WORD_CAP / 2:.2f}" font-family="Noto Sans" font-weight="700"
        font-size="170" letter-spacing="-1.7" fill="{colours['word']}">Procyon</text>'''
    if not TAG:
        return word, x0 + WORD_W
    tag_x = x0 + WORD_W + GAP
    tag_w = TAG_TEXT_W + 2 * TAG_PAD
    return word + f'''
  <rect x="{tag_x:.2f}" y="{MID - TAG_H / 2:.2f}" width="{tag_w:.2f}" height="{TAG_H}" rx="{TAG_R}" fill="{colours['tag']}"/>
  <text x="{tag_x + TAG_PAD - TAG_LEFT:.2f}" y="{MID + TAG_CAP / 2:.2f}" font-family="DejaVu Sans Mono"
        font-weight="700" font-size="100" letter-spacing="5" fill="{colours['tag_text']}">{TAG}</text>''', tag_x + tag_w


def with_tagline(colours):
    """ "Procyon" with the tagline underneath; (svg, width)."""
    marks, right = wordmark(0, colours)
    return marks + f'''
  <text x="0" y="{LINE_Y:.2f}" font-family="DejaVu Sans Mono" font-weight="700" font-size="{LINE_SIZE}"
        textLength="{right:.2f}" lengthAdjust="spacing" fill="{colours['line']}">{TAGLINE}</text>''', right


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
                   f"<title>Procyon</title>{icon}{marks}\n</svg>\n")
            outline(svg, os.path.join(HERE, name + ".svg"))
            png(os.path.join(HERE, name + ".svg"), os.path.join(HERE, name + ".png"), 4000)
            made.append(name)
    # Stacked: raccoon on top, "Procyon" underneath
    for bg, colours in COLOURS.items():
        for flat in (False, True):
            marks, right = wordmark(0, colours)
            dy = STACK_ICON + STACK_GAP - ROW_TOP
            icon = (f'<g transform="translate({(right - STACK_ICON) / 2:.2f} 0) scale({STACK_ICON / 32})">'
                    f"{icon_body(flat)}</g>")
            name = f"procyon-logo-stacked-{bg}-bg{'-flat' if flat else ''}"
            svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {right:.0f} {dy + ROW_BOTTOM:.0f}">'
                   f'<title>Procyon</title>{icon}<g transform="translate(0 {dy:.2f})">{marks}</g>\n</svg>\n')
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
    # With the tagline: side by side (cards, banners) and stacked (stickers)
    top = MID - WORD_CAP / 2                       # top of the capital P
    for bg, colours in COLOURS.items():
        marks, right = with_tagline(colours)
        size = 300                                 # the icon spans "Procyon" and the tagline
        dy = (size - (LINE_Y - top)) / 2 - top
        name = f"procyon-logo-tagline-{bg}-bg"
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size + GAP + right:.0f} {size}">'
               f'<title>Procyon</title><g transform="scale({size / 32})">{icon_body()}</g>'
               f'<g transform="translate({size + GAP} {dy:.2f})">{marks}</g>\n</svg>\n')
        outline(svg, os.path.join(HERE, name + ".svg"))
        png(os.path.join(HERE, name + ".svg"), os.path.join(HERE, name + ".png"), 4000)
        made.append(name)
        icon = 420                                 # smaller raccoon than the plain stacked logo,
        dy = icon + 60 - top                       # so the tagline stays readable on a sticker
        name = f"procyon-logo-stacked-tagline-{bg}-bg"
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {right:.0f} {dy + LINE_Y + 12:.0f}">'
               f'<title>Procyon</title><g transform="translate({(right - icon) / 2:.2f} 0) '
               f'scale({icon / 32})">{icon_body()}</g>'
               f'<g transform="translate(0 {dy:.2f})">{marks}</g>\n</svg>\n')
        outline(svg, os.path.join(HERE, name + ".svg"))
        png(os.path.join(HERE, name + ".svg"), os.path.join(HERE, name + ".png"), 3000)
        made.append(name)
    # About card: "Procyon" and the tagline as two shapes on the same canvas,
    # used as CSS masks so each palette can colour them (text and teal)
    black = {"word": "#000", "tag": "#000", "tag_text": "#000", "line": "#000"}
    marks, right = wordmark(0, black)
    line = with_tagline(black)[0][len(marks):]          # just the tagline's <text>
    top, bottom = MID - WORD_CAP / 2 - 4, LINE_Y + 12
    box = f'viewBox="0 {top:.2f} {right:.2f} {bottom - top:.2f}"'
    for name, body in (("about-word", marks), ("about-tagline", line)):
        outline(f'<svg xmlns="http://www.w3.org/2000/svg" {box}>{body}\n</svg>\n',
                os.path.join(HERE, "..", "procyon", "web", name + ".svg"))
        made.append(f"procyon/web/{name}.svg")
    # Wordmark for the app header (the icon is shown separately there)
    marks, right = wordmark(0, COLOURS["dark"])
    outline(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {right:.0f} {ICON_SIZE}">'
            f"<title>Procyon</title>{marks}\n</svg>\n",
            os.path.join(HERE, "..", "procyon", "web", "wordmark.svg"))
    made.append("procyon/web/wordmark.svg")
    print("made:", ", ".join(made))


if __name__ == "__main__":
    main()
