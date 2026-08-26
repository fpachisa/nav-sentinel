"""Speak the narration and measure how long it actually takes.

Word counts were a proxy for duration and a bad one: they assume a speaking rate, and the rate is
the thing nobody knows until a voice has read the script. This renders the narration to audio with
`say`, times each scene with `afinfo`, and prints where the four minutes go.

`say` is not the voice that will be used, so the absolute number is an estimate. The *shape* is not
an estimate: which scene is long, and by how much, is the same whoever reads it. Run it at a couple
of rates to bracket the answer.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NARRATION = ROOT / "docs" / "submission" / "narration.md"

#: Written for a reader, spoken by a synthesiser. `&mdash;` is a pause, not a word.
SUBSTITUTIONS = (
    ("&mdash;", ","),
    ("&nbsp;", " "),
    ("*", ""),
    ("`", ""),
)


def scenes(text: str) -> list[tuple[str, str]]:
    """(heading, spoken text) for every scene that has narration."""
    out: list[tuple[str, str]] = []
    heading = "opening"
    spoken: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            if spoken:
                out.append((heading, " ".join(spoken)))
            heading, spoken = line[3:].split("·")[0].strip(), []
        elif line.startswith("> "):
            spoken.append(line[2:].strip())
    if spoken:
        out.append((heading, " ".join(spoken)))
    return out


def speakable(line: str) -> str:
    for old, new in SUBSTITUTIONS:
        line = line.replace(old, new)
    return re.sub(r"\s+", " ", line).strip()


def duration(path: Path) -> float:
    info = subprocess.run(
        ["afinfo", str(path)], capture_output=True, text=True, check=True
    ).stdout
    match = re.search(r"estimated duration: ([\d.]+) sec", info)
    if not match:
        raise SystemExit(f"afinfo gave no duration for {path}:\n{info}")
    return float(match.group(1))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--voice", default="Daniel")
    parser.add_argument("--rate", type=int, default=0, help="words per minute; 0 = voice default")
    parser.add_argument("--gap", type=float, default=2.5, help="seconds of silence held per scene")
    parser.add_argument("--out", type=Path, default=Path("build/narration"))
    parser.add_argument("--cap", type=float, default=240.0)
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    found = scenes(NARRATION.read_text())
    if not found:
        raise SystemExit("no narration found — has the format changed?")

    total = 0.0
    print(f"voice={args.voice} rate={args.rate or 'default'}\n")
    print(f"{'scene':34} {'words':>6} {'spoken':>8} {'wpm':>6}")
    print("-" * 58)
    for index, (heading, text) in enumerate(found):
        path = args.out / f"{index:02d}.aiff"
        command = ["say", "-v", args.voice, "-o", str(path)]
        if args.rate:
            command += ["-r", str(args.rate)]
        subprocess.run([*command, speakable(text)], check=True)
        seconds = duration(path)
        words = len(re.findall(r"[\w'’-]+", text))
        total += seconds
        print(f"{heading[:34]:34} {words:6} {seconds:7.1f}s {words / seconds * 60:6.0f}")

    # One file to listen to, with the scene pauses actually in it, so the number above can be
    # checked against a clock instead of trusted.
    joined = args.out / "narration.aiff"
    pause = f" [[slnc {int(args.gap * 1000)}]] "
    command = ["say", "-v", args.voice, "-o", str(joined)]
    if args.rate:
        command += ["-r", str(args.rate)]
    subprocess.run([*command, pause.join(speakable(text) for _h, text in found)], check=True)

    silence = args.gap * len(found)
    print("-" * 58)
    print(f"{'speech':34} {'':6} {total:7.1f}s")
    print(f"{'+ ' + str(len(found)) + ' pauses at ' + str(args.gap) + 's':34} {'':6} {silence:7.1f}s")
    print(f"{'TOTAL':34} {'':6} {total + silence:7.1f}s   cap {args.cap:.0f}s")
    over = total + silence - args.cap
    print(f"\n{'OVER by ' + format(over, '.1f') + 's' if over > 0 else 'Fits, with ' + format(-over, '.1f') + 's spare'}")
    print(f"measured end to end: {duration(joined):.1f}s  ->  {joined}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
