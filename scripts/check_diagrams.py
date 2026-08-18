"""Geometric overlap check for the architecture figures.

Three label collisions in a row got through visual inspection, so the check is mechanical
now. It approximates each text run's extent from its character count and font size, then
reports any run whose box intersects a rect it is not the label of.

Approximate by design: it is a smoke test for gross collisions, not a text-metrics engine.
Run it after editing docs/architecture.html.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Average glyph width as a fraction of font-size, measured off the rendered output.
WIDTH_RATIO = {"mono": 0.62, "sans": 0.55}
DOC = Path(__file__).resolve().parents[1] / "docs" / "architecture.html"


def text_extent(txt: str, size: float, family: str, x: float, anchor: str):
    ratio = WIDTH_RATIO["mono"] if "Mono" in family else WIDTH_RATIO["sans"]
    w = len(txt) * size * ratio
    if anchor == "middle":
        return x - w / 2, x + w / 2
    if anchor == "end":
        return x - w, x
    return x, x + w


def main() -> int:
    src = DOC.read_text()
    figures = re.findall(r'<svg viewBox="0 0 1040 (\d+)"(.*?)</svg>', src, re.S)
    failures = 0

    for n, (_height, fig) in enumerate(figures, 1):
        # A halo rect immediately preceding a text run means the overlap is deliberate.
        haloed = set(re.findall(r'<rect x="(\d+)" y="(\d+)"[^>]*fill="var\(--paper-sunk\)"', fig))
        halo_x = {float(x) for x, _ in haloed}

        rects = []
        for m in re.finditer(
            r'<rect x="([\d.]+)" y="([\d.]+)" width="([\d.]+)" height="([\d.]+)"([^>]*)', fig
        ):
            x, y, w, h, attrs = (*map(float, m.groups()[:4]), m.group(5))
            if "paper-sunk" in attrs:      # halo or canvas ground, not a content box
                continue
            if w > 900:                    # full-width band; labels legitimately sit on it
                continue
            rects.append((x, y, w, h))

        hits = []
        for m in re.finditer(r'<text x="([\d.]+)" y="([\d.]+)"([^>]*)>([^<]*)</text>', fig):
            x, y, attrs, txt = float(m.group(1)), float(m.group(2)), m.group(3), m.group(4)
            if not txt.strip():
                continue
            size = float(s.group(1)) if (s := re.search(r'font-size="([\d.]+)"', attrs)) else 10.0
            fam = f.group(1) if (f := re.search(r'font-family="([^"]+)"', attrs)) else "sans"
            anc = a.group(1) if (a := re.search(r'text-anchor="(\w+)"', attrs)) else "start"
            x0, x1 = text_extent(txt, size, fam, x, anc)
            top, bottom = y - size * 0.78, y + size * 0.22
            if any(abs(x0 - hx) < 4 for hx in halo_x):
                continue                   # deliberate, has a halo behind it

            for rx, ry, rw, rh in rects:
                inside_x = x0 >= rx - 1 and x1 <= rx + rw + 1
                inside_y = top >= ry - 1 and bottom <= ry + rh + 1
                if inside_x and inside_y:
                    continue               # a label of that box
                if x0 < rx + rw and x1 > rx and top < ry + rh and bottom > ry:
                    hits.append(f'"{txt[:30]}" ({x:.0f},{y:.0f}) straddles rect ({rx:.0f},{ry:.0f})')

        status = "ok" if not hits else f"{len(hits)} COLLISION(S)"
        print(f"  figure {n}: {status}")
        for h in dict.fromkeys(hits):
            print(f"      {h}")
        failures += len(hits)

    # No connector may be diagonal; the set is orthogonal by convention.
    skew = 0
    for n, (_h, fig) in enumerate(figures, 1):
        for m in re.finditer(r'<line x1="([\d.]+)" y1="([\d.]+)" x2="([\d.]+)" y2="([\d.]+)"', fig):
            x1, y1, x2, y2 = map(float, m.groups())
            if x1 != x2 and y1 != y2:
                print(f"  figure {n}: skewed connector ({x1:.0f},{y1:.0f})->({x2:.0f},{y2:.0f})")
                skew += 1

    total = failures + skew
    print(f"\n  {'PASS' if total == 0 else f'FAIL — {total} issue(s)'}")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
