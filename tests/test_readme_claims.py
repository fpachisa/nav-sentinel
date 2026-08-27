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
        # And the other direction, which was missing. One-sided, this caught a README naming a
        # policy the code lacked and never a policy the code emitted that the README omitted --
        # which is what actually happened: P-009 was enforced, cited in prose, and in no table row.
        assert in_code <= in_readme, (
            f"the code emits policy ids the README does not document: {sorted(in_code - in_readme)}"
        )
        assert policies.band_for is not None

    def test_the_readme_counts_the_policies_it_documents(self):
        """The heading said eight over a table of eight while the code emitted nine, and four other
        sentences still said seven. A count written as a word is a claim, and nothing read it."""
        import re

        words = {
            "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
            "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
        }
        source = (ROOT / "src" / "nav_sentinel" / "control_plane" / "policies.py").read_text()
        emitted = len(set(re.findall(r'policy_id="(P-\d+-[A-Z-]+)"', source)))

        claimed = {
            words[m.lower()]
            for m in re.findall(r"\b(\w+) (?:enforced )?policies\b", README)
            if m.lower() in words
        }
        assert claimed, "README states no policy count in words"
        assert claimed == {emitted}, (
            f"README states policy counts {sorted(claimed)}; the code emits {emitted}"
        )

    def test_license_claim_has_a_file(self):
        assert "MIT" in README
        assert (ROOT / "LICENSE").exists(), "README declares MIT with no LICENSE file"


DEVPOST = (ROOT / "docs" / "submission" / "devpost.md").read_text()


class TestTheSubmissionCopyIsTrue:
    """The Devpost text is the version of these claims that strangers read.

    It repeats numbers the README also carries, which means it can rot independently -- and it
    rots somewhere nobody re-reads, in front of judges, after the code has moved on. It already
    disagreed with the code once: it advertised five investigating specialists when the registry
    publishes three, because two are deliberately unpublished so their breaks escalate. The
    README made the same claim in its opening while contradicting it in its own evidence table.
    """

    def test_the_test_count_matches_the_suite(self):
        claimed = {int(n) for n in re.findall(r"(\d+) offline tests", DEVPOST)}
        assert claimed, "the submission copy no longer states a suite size"
        assert claimed == {_collected("tests/")}, (
            f"devpost.md claims {sorted(claimed)} offline tests; pytest collects {_collected('tests/')}"
        )

    def test_it_names_only_specialists_the_registry_will_actually_route_to(self):
        """An unpublished agent is not a capability the fleet has."""
        from nav_sentinel import composition
        from nav_sentinel.registry import discover

        composition.configure()
        published = {a.agent_id for a in discover.all_agents()}
        for gap in ("pricing-investigator", "cash-fees-investigator"):
            assert gap not in published, (
                f"{gap} is published now -- the submission copy says it is a deliberate coverage "
                f"gap that escalates to a human, and that is no longer true"
            )

    def test_the_process_and_policy_counts_are_real(self):
        from nav_sentinel import composition
        from nav_sentinel.control_plane import packs

        composition.configure()
        assert len(packs.registered()) == 3, "devpost.md says three processes plug into the seam"

        policies = (ROOT / "src" / "nav_sentinel" / "control_plane" / "policies.py").read_text()
        assert len(set(re.findall(r"P-0\d\d", policies))) == 10, (
            "devpost.md says ten policies are enforced in code"
        )

    def test_the_deployed_url_it_sends_judges_to_is_the_one_that_can_sign_in(self):
        """Cloud Run publishes two hostnames for this service and only one is a registered
        OAuth JavaScript origin. On the other, Google's button renders and then refuses."""
        runbook = (ROOT / "docs" / "submission" / "recording-runbook.md").read_text()
        signed_in_host = "nav-sentinel-rwkxhtvoeq-uc.a.run.app"
        assert signed_in_host in DEVPOST
        assert signed_in_host in runbook
        assert "nav-sentinel-523099900380.us-central1.run.app/app" not in DEVPOST


class TestTheDepartmentCountIsTheRegisteredOne:
    """The README and the narration both said "four departments" while three processes are
    registered and the Fleet page renders "across 3 departments". A number that contradicts the
    screen behind it is worse in a video than a vaguer claim would have been."""

    def test_no_document_claims_more_departments_than_the_seam_hosts(self):
        from nav_sentinel import composition
        from nav_sentinel.control_plane import packs

        composition.configure()
        hosted = len(packs.registered())
        words = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven"}
        narration = (ROOT / "docs" / "submission" / "narration.md").read_text()

        for name, text in (("README.md", README), ("devpost.md", DEVPOST),
                           ("narration.md", narration)):
            for count, word in words.items():
                if count == hosted:
                    continue
                for phrase in (f"{word} departments", f"{count} departments"):
                    assert phrase not in text.lower(), (
                        f"{name} says {phrase!r}; the seam hosts {hosted} processes"
                    )

    def test_the_narration_and_the_fleet_page_agree_on_unhandled_capabilities(self):
        """Scene 2 names a number that is on screen while it is said."""
        from nav_sentinel import composition
        from nav_sentinel.registry import discover

        composition.configure()
        coverage = discover.coverage()
        gaps = sum(
            1
            for c, ref in coverage.items()
            if ref is None and not c.endswith(".unclassified")
        )
        narration = (ROOT / "docs" / "submission" / "narration.md").read_text()
        words = {3: "Three", 4: "Four", 5: "Five", 6: "Six", 7: "Seven"}
        # The wording moved from shot 2 to shot 5, where the routing table is on screen and the
        # number is legible behind the claim. The property is unchanged: whatever the script says
        # out loud must be what the page computes.
        assert f"{words[gaps]} kinds of break here have nobody" in narration, (
            f"there are {gaps} unhandled capabilities; the narration names a different number"
        )


class TestTheTaggedNarrationSaysTheSameThing:
    """`narration-tts.md` is the script with TTS style tags in it, and it is the file that will
    actually be pasted into a voice. Two copies of the same words drift, and the copy that drifts
    is the one nobody re-measures -- so the words are compared, and the tags are required to be
    tags rather than words that will be read aloud.
    """

    TAG = re.compile(r"\[[a-z-]+\]")

    def _spoken(self, path: Path, fenced: bool) -> list[str]:
        text = path.read_text()
        if fenced:
            blocks = re.findall(r"^```\n(.*?)^```", text, re.MULTILINE | re.DOTALL)
            body = "\n".join(blocks)
        else:
            body = "\n".join(
                line[2:] for line in text.splitlines() if line.startswith("> ")
            )
        body = self.TAG.sub(" ", body).replace("&mdash;", "—").replace("*", "")
        return re.findall(r"[\w'’-]+", body.lower())

    def test_the_words_are_identical_once_the_tags_are_removed(self):
        plain = self._spoken(ROOT / "docs" / "submission" / "narration.md", fenced=False)
        tagged = self._spoken(ROOT / "docs" / "submission" / "narration-tts.md", fenced=True)
        assert tagged, "no fenced blocks found in narration-tts.md"
        if plain != tagged:
            for index, (a, b) in enumerate(zip(plain, tagged, strict=False)):
                if a != b:
                    raise AssertionError(
                        f"the two scripts diverge at word {index}: "
                        f"narration.md has {' '.join(plain[index:index + 8])!r}, "
                        f"narration-tts.md has {' '.join(tagged[index:index + 8])!r}"
                    )
            raise AssertionError(
                f"lengths differ: narration.md {len(plain)} words, "
                f"narration-tts.md {len(tagged)} words"
            )

    def test_every_tag_is_one_the_voice_will_treat_as_a_tag(self):
        """A typo'd or invented tag is read out loud. `[emphatic]` becomes the word "emphatic"."""
        allowed = {
            "serious", "explanation", "informative", "neutral", "emphatic", "calm",
            "instruction", "matter-of-fact", "approval", "reminder",
        }
        text = (ROOT / "docs" / "submission" / "narration-tts.md").read_text()
        blocks = re.findall(r"^```\n(.*?)^```", text, re.MULTILINE | re.DOTALL)
        used = {tag.strip("[]") for block in blocks for tag in self.TAG.findall(block)}
        assert used, "no tags found"
        assert used <= allowed, f"unrecognised tags: {sorted(used - allowed)}"

    def test_the_measured_script_carries_no_tags(self):
        """`make narration` speaks `narration.md` with `say`, which would read a tag aloud and
        inflate every measurement by a word it will never say."""
        plain = (ROOT / "docs" / "submission" / "narration.md").read_text()
        spoken = "\n".join(line for line in plain.splitlines() if line.startswith("> "))
        assert not self.TAG.search(spoken), self.TAG.findall(spoken)
