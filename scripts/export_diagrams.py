"""Export the inline figures from docs/architecture.html as standalone files.

The page styles its SVGs with CSS custom properties so they follow the reader's theme. Those
properties do not resolve outside the document, so a standalone copy needs literal colours
and an explicitly painted ground -- without the ground rect the file rasterises transparent,
which most viewers show as black on black.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "architecture.html"
OUT = ROOT / "docs" / "diagrams"

LIGHT = {
    "--ink": "#10151C", "--ink-soft": "#46525F", "--ink-faint": "#7C8894",
    "--paper": "#FBFCFD", "--paper-sunk": "#F1F4F7", "--rule": "#D6DCE4",
    "--accent": "#1F5C8B", "--accent-wash": "#E6EFF6",
    "--deny": "#A8322A", "--closed": "#2C6E5A",
}
FIGURES = [
    ("01-system", "NAV Sentinel — system architecture"),
    ("02-enforcement-path", "NAV Sentinel — what happens to a tool call"),
    ("03-quarantine-boundary", "NAV Sentinel — the quarantine boundary"),
    ("04-nav-closure", "NAV Sentinel — definition of done is arithmetic"),
]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    blocks = re.findall(r'(<svg viewBox="0 0 1040 \d+".*?</svg>)', DOC.read_text(), re.S)
    if len(blocks) != len(FIGURES):
        raise SystemExit(f"expected {len(FIGURES)} figures, found {len(blocks)}")

    for (name, title), svg in zip(FIGURES, blocks):
        s = svg
        for token, value in LIGHT.items():
            s = s.replace(f"var({token})", value)
        w, h = re.search(r'viewBox="0 0 (\d+) (\d+)"', s).groups()
        s = s.replace(
            "<svg viewBox=",
            f'<svg xmlns="http://www.w3.org/2000/svg" color="{LIGHT["--ink"]}" '
            f'font-family="Archivo, Helvetica Neue, Arial, sans-serif" viewBox=',
            1,
        )
        s = s.replace(
            "</defs>",
            f'</defs>\n        <rect x="0" y="0" width="{w}" height="{h}" '
            f'fill="{LIGHT["--paper-sunk"]}"/>',
            1,
        )
        s = s.replace("<defs>", f"<title>{title}</title>\n  <defs>", 1)
        if "var(--" in s:
            raise SystemExit(f"{name}: unresolved custom property")

        (OUT / f"{name}.svg").write_text(s + "\n")
        png = OUT / f"{name}.png"
        if subprocess.run(
            ["rsvg-convert", "-w", "2080", str(OUT / f"{name}.svg"), "-o", str(png)],
            capture_output=True, check=False,
        ).returncode == 0:
            print(f"  {name}.svg + .png")
        else:
            print(f"  {name}.svg (rsvg-convert unavailable; PNG not refreshed)")


if __name__ == "__main__":
    main()
