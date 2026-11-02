"""Background textures for the colour palettes, as small tiling SVGs (our own
drawings, made from fixed random seeds so they rebuild identically):
starfield (Night sky), maple grain (Antique radio), pine needles (Campfire).
    python3 branding/make_textures.py      -> procyon/web/tex/*.svg
"""
import math
import os
import random

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "procyon", "web", "tex")

def stars(size, n, seed, rmin, rmax, omin, omax, big=0):
    rnd = random.Random(seed)
    out = []
    for i in range(n):
        x, y = rnd.uniform(0, size), rnd.uniform(0, size)
        c = rnd.random()
        col = "#ffffff" if c < .74 else "#b4ccff" if c < .89 else "#ffe6a6" if c < .95 else "#ffb49c"
        r, o = rnd.uniform(rmin, rmax), rnd.uniform(omin, omax)
        if i < big:   # a few brighter ones with a soft halo
            r, o = rnd.uniform(1.0, 1.4), rnd.uniform(.55, .75)
            out.append(f"<circle cx='{x:.1f}' cy='{y:.1f}' r='{r * 3.2:.1f}' fill='{col}' opacity='{o * .12:.3f}'/>")
        out.append(f"<circle cx='{x:.1f}' cy='{y:.1f}' r='{r:.2f}' fill='{col}' opacity='{o:.2f}'/>")
    return f"<svg xmlns='http://www.w3.org/2000/svg' width='{size}' height='{size}'>{''.join(out)}</svg>"

star_near = stars(709, 76, 11, .55, .95, .30, .55, big=5)
star_far = stars(1013, 158, 23, .35, .6, .14, .28)

maple = ("<svg xmlns='http://www.w3.org/2000/svg' width='640' height='640'>"
         "<filter id='d' x='0' y='0' width='100%' height='100%'>"
         "<feTurbulence type='fractalNoise' baseFrequency='0.004 0.16' numOctaves='3' seed='7' stitchTiles='stitch'/>"
         "<feColorMatrix type='matrix' values='0 0 0 0 0.25  0 0 0 0 0.12  0 0 0 0 0.03  0 0 0 1.1 -0.42'/></filter>"
         "<filter id='l' x='0' y='0' width='100%' height='100%'>"
         "<feTurbulence type='fractalNoise' baseFrequency='0.0025 0.06' numOctaves='2' seed='31' stitchTiles='stitch'/>"
         "<feColorMatrix type='matrix' values='0 0 0 0 1  0 0 0 0 0.88  0 0 0 0 0.66  0 0 0 0.9 -0.48'/></filter>"
         "<rect width='100%' height='100%' filter='url(#d)'/><rect width='100%' height='100%' filter='url(#l)'/></svg>")

def needles(size, seed):
    rnd = random.Random(seed)
    lines = []
    def needle(x, y, ang, length, col, op, w=.9):
        bend = rnd.uniform(-.12, .12)
        x2, y2 = x + math.cos(ang) * length, y + math.sin(ang) * length
        cx = (x + x2) / 2 + math.cos(ang + math.pi / 2) * length * bend
        cy = (y + y2) / 2 + math.sin(ang + math.pi / 2) * length * bend
        for dx in (-size, 0, size):          # wrap around the tile edges
            for dy in (-size, 0, size):
                if -30 < x + dx < size + 30 and -30 < y + dy < size + 30:
                    lines.append(f"<path d='M{x+dx:.1f} {y+dy:.1f}Q{cx+dx:.1f} {cy+dy:.1f} {x2+dx:.1f} {y2+dy:.1f}' "
                                 f"stroke='{col}' stroke-opacity='{op:.2f}' stroke-width='{w}' fill='none' stroke-linecap='round'/>")
    greens, browns = ["#3f6b4a", "#4f7a52", "#365e44"], ["#6b4c30", "#7a5a38"]
    for _ in range(29):                       # bundles of 2-5, joined at the base
        x, y, base = rnd.uniform(0, size), rnd.uniform(0, size), rnd.uniform(0, 2 * math.pi)
        col = rnd.choice(browns if rnd.random() < .35 else greens)
        op = rnd.uniform(.16, .28)
        length = rnd.uniform(16, 28)
        for _ in range(rnd.randint(2, 5)):
            needle(x, y, base + rnd.uniform(-.35, .35), length * rnd.uniform(.85, 1.05), col, op)
    for _ in range(79):                       # loose single needles
        needle(rnd.uniform(0, size), rnd.uniform(0, size), rnd.uniform(0, 2 * math.pi), rnd.uniform(10, 24),
               rnd.choice(greens + browns), rnd.uniform(.10, .20), .8)
    return f"<svg xmlns='http://www.w3.org/2000/svg' width='{size}' height='{size}'>{''.join(lines)}</svg>"

pine = needles(560, 5)
for name, svg in (("stars-near", star_near), ("stars-far", star_far), ("maple", maple), ("pine", pine)):
    with open(os.path.join(OUT, name + ".svg"), "w") as f:
        f.write(svg)
