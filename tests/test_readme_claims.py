"""Guard the README's factual claims against the code.

The README carries a Status table and a Known defects list whose whole value is that a reader
can trust them. They rotted once: a test-count claim of 32 survived until the suite reached 65,
and the disclaimer "no test in the suite currently touches Model Armor" outlived the commit
that added tests touching Model Armor. A stale honesty section is worse than none, because it
is read as current.

These tests fail when a claim drifts from reality, so the README cannot silently go out of date
again.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text()


def _collected(target: str) -> int:
    """Count tests pytest collects for a target, without running them."""
    out = subprocess.run(
        [sys.executable, "-m", "pytest", target, "--collect-only", "-q", "-p", "no:cacheprovider"],
        capture_output=True, text=True, cwd=ROOT, check=False,
    ).stdout
    # "12/16 tests collected (4 deselected)" -> 12 selected, which is what `make test` runs
    # and therefore what the README's count refers to.
    m = re.search(r"(\d+)/(\d+) tests collected", out)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+) tests? collected", out)
    return int(m.group(1)) if m else 0


class TestClaimedCounts:
    def test_readme_total_test_count_is_current(self):
        """Every number the README quotes as a suite size must be the real one."""
        claimed = {int(n) for n in re.findall(r"(\d+) invariant tests", README)}
        claimed |= {int(n) for n in re.findall(r"all (\d+) tests passing", README)}
        actual = _collected("tests/")
        assert claimed, "no suite-size claim found in README — remove this test or restore the claim"
        assert claimed == {actual}, (
            f"README claims suite sizes {sorted(claimed)}; pytest collects {actual}"
        )

    def test_reconciliation_test_count_is_current(self):
        m = re.search(r"tests/test_reconciliation\.py`? \((\d+) tests\)", README)
        assert m, "README no longer states a count for test_reconciliation.py"
        actual = _collected("tests/test_reconciliation.py")
        assert int(m.group(1)) == actual, (
            f"README claims {m.group(1)} reconciliation tests; pytest collects {actual}"
        )


class TestClaimedCoverage:
    def test_model_armor_disclaimer_matches_reality(self):
        """The disclaimer must not understate coverage that now exists, nor overstate what the
        tests actually prove. Both directions are honesty failures."""
        touching = [
            p.name for p in sorted((ROOT / "tests").glob("test_*.py"))
            if "model_armor" in p.read_text()
        ]
        says_none = "No test in the suite currently touches Model Armor" in README
        assert not (touching and says_none), (
            f"README says no test touches Model Armor, but {touching} do"
        )
        if touching:
            assert "monkeypatch" in README, (
                "tests touch Model Armor but the README does not disclose that they stub the "
                "service rather than exercising it"
            )

    def test_every_known_defect_is_still_open(self):
        """A defect list that keeps fixed items is as misleading as one that omits live ones."""
        assert "make demo" in README, "README should still record the make demo defect"
        demo = subprocess.run(
            [sys.executable, "-m", "nav_sentinel.pipeline.orchestrator"],
            capture_output=True, text=True, cwd=ROOT, check=False,
        )
        assert demo.returncode != 0, (
            "README lists `make demo` as broken, but the module now runs — update the defect list"
        )

    def test_license_claim_has_a_file(self):
        assert "MIT" in README
        assert (ROOT / "LICENSE").exists(), "README declares MIT with no LICENSE file"
