"""Mechanical checks on the architecture figures.

Scope note, learned the hard way. An earlier version of this script tried to detect
label-over-box collisions by estimating each text run's pixel extent. It reported 42
collisions, of which zero were real: it ignored `font-size` and `font-family` inherited from
enclosing <g> elements, so every estimate was wrong. Worse, the distinction it was reaching
for is semantic rather than geometric -- a label legitimately sits inside its own box, an
annotation legitimately sits outside every box, and geometry alone cannot tell which is
which. A check that cries wolf is worse than no check, so that heuristic is gone.

What remains is exact and worth trusting:

  * no connector is diagonal -- the figures are orthogonal by convention
  * nothing is drawn outside its own viewBox, which would be silently clipped
  * every marker referenced by url(#id) is defined in the same fragment
  * no construct the Artifact CSP forbids inside inline SVG

Overlap remains a visual review item. Render docs/diagrams/*.png and look at them; three
collisions shipped past inspection during this build, so looking is not optional.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

DOC = Path(__file__).resolve().parents[1] / "docs" / "architecture.html"


def figures(src: str) -> list[tuple[int, int, str]]:
    out = []
    for m in re.finditer(r'<svg viewBox="0 0 (\d+) (\d+)"(.*?)</svg>', src, re.DOTALL):
        out.append((int(m.group(1)), int(m.group(2)), m.group(3)))
    return out


def main() -> int:
    src = DOC.read_text()
    figs = figures(src)
    if not figs:
        print("  no figures found")
        return 1

    issues: list[str] = []

    for n, (vw, vh, fig) in enumerate(figs, 1):
        fig_issues: list[str] = []

        # 1. Orthogonal connectors only.
        for m in re.finditer(r'<line x1="([\d.]+)" y1="([\d.]+)" x2="([\d.]+)" y2="([\d.]+)"', fig):
            x1, y1, x2, y2 = map(float, m.groups())
            if x1 != x2 and y1 != y2:
                fig_issues.append(f"diagonal connector ({x1:.0f},{y1:.0f})->({x2:.0f},{y2:.0f})")

        # 2. Nothing outside the viewBox. A shape past the edge is clipped without warning.
        for m in re.finditer(r'<rect x="(-?[\d.]+)" y="(-?[\d.]+)" width="([\d.]+)" height="([\d.]+)"', fig):
            x, y, w, h = map(float, m.groups())
            if x < 0 or y < 0 or x + w > vw or y + h > vh:
                fig_issues.append(f"rect ({x:.0f},{y:.0f},{w:.0f}x{h:.0f}) exceeds viewBox {vw}x{vh}")
        for m in re.finditer(r'<text x="(-?[\d.]+)" y="(-?[\d.]+)"', fig):
            x, y = map(float, m.groups())
            if not (0 <= x <= vw and 0 <= y <= vh):
                fig_issues.append(f"text anchor ({x:.0f},{y:.0f}) outside viewBox {vw}x{vh}")

        # 3. Every referenced marker is defined in this fragment.
        defined = set(re.findall(r'<marker id="([^"]+)"', fig))
        for ref in set(re.findall(r'url\(#([^)]+)\)', fig)):
            if ref not in defined:
                fig_issues.append(f"marker #{ref} referenced but not defined")

        # 4. Constructs the Artifact CSP forbids inside inline SVG.
        for bad in ("<script", "<foreignObject", "<style", 'href="http'):
            if bad in fig:
                fig_issues.append(f"forbidden construct {bad!r}")

        print(f"  figure {n} ({vw}x{vh}): {'ok' if not fig_issues else f'{len(fig_issues)} issue(s)'}")
        for i in fig_issues:
            print(f"      {i}")
        issues += fig_issues

    print(f"\n  {'PASS' if not issues else f'FAIL — {len(issues)} issue(s)'}")
    print("  (label overlap is not checked mechanically — review docs/diagrams/*.png by eye)")
    return 1 if issues else 0


if __name__ == "__main__":
    sys.exit(main())
