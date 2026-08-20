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
        if not touching:
            return

        # Two kinds of coverage now exist and the README must distinguish them, because "tested"
        # means something different for each: a stubbed test proves the gateway's wiring, a live
        # one proves the service's behaviour.
        stubs = any(
            "monkeypatch" in (ROOT / "tests" / name).read_text()
            and "model_armor" in (ROOT / "tests" / name).read_text()
            for name in touching
        )
        live = any(
            "mark.live" in (ROOT / "tests" / name).read_text()
            and "model_armor" in (ROOT / "tests" / name).read_text()
            for name in touching
        )
        if stubs:
            assert "stub" in README.lower() or "monkeypatch" in README, (
                "some Model Armor tests stub the service and the README does not say so"
            )
        if live:
            assert "live" in README.lower(), (
                "live Model Armor tests exist and the README does not mention them"
            )

    def test_the_walkthrough_commands_the_readme_promises_actually_run(self):
        """The replacement for a test that could not fail.

        It asserted `make demo` was broken by running `nav_sentinel.pipeline.orchestrator` and
        checking for a non-zero exit. That module has never existed in this tree, so the assertion
        passed on the import error -- guarding a defect that was closed, in the one file whose job
        is stopping claim drift, and it stayed green through a full rewrite of the spin-up section
        that deleted the "NOT YET IMPLEMENTED" line it was supposedly protecting.

        Inverted: the README now walks a reader through these commands, so they have to work. Only
        the offline ones -- a test must not spend model calls.
        """
        for module, promise in (
            ("nav_sentinel.pipeline.cycle_runner", "make demo"),
            ("nav_sentinel.fleet_cli", "make registry"),
        ):
            assert promise in README, f"the README no longer mentions {promise}"
            run = subprocess.run(
                [sys.executable, "-m", module],
                capture_output=True, text=True, cwd=ROOT, check=False,
            )
            assert run.returncode == 0, (
                f"the README walks a reader through `{promise}` and {module} exits "
                f"{run.returncode}:\n{run.stderr[-600:]}"
            )

    def test_policy_ids_in_the_readme_exist_in_the_code(self):
        """The policy table drifted once: it named P-004-MATERIALITY-ROUTING after the code had
        renamed it, and described the band as basis-point-driven after that coupling was removed."""
        import re

        from nav_sentinel.control_plane import policies

        source = (ROOT / "src" / "nav_sentinel" / "control_plane" / "policies.py").read_text()
        in_code = set(re.findall(r'policy_id="(P-\d+-[A-Z-]+)"', source))
        in_readme = set(re.findall(r"\b(P-\d+-[A-Z-]+)\b", README))
        assert in_readme, "README no longer names any policy id"
        assert in_readme <= in_code, (
            f"README names policy ids the code does not emit: {sorted(in_readme - in_code)}"
        )
        assert policies.band_for is not None

    def test_license_claim_has_a_file(self):
        assert "MIT" in README
        assert (ROOT / "LICENSE").exists(), "README declares MIT with no LICENSE file"
