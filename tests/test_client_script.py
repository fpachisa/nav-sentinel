"""The JavaScript the desk serves must actually parse.

It did not, and nothing noticed. A `\\n` escape written inside a non-raw Python string became a
literal newline inside a JS string literal -- a `SyntaxError` that killed the whole script, so the
streaming handler never attached and the form silently fell back to a plain POST. On screen that
looks like "the feature is slow", not "the feature is not running".

**Both checks I had passed while it was broken**, which is the part worth fixing:

- the test asserted `b.disabled=true` appeared in the page, and that is the *inline* `onsubmit`
  attribute, which works with or without the script;
- the live check curled the stream endpoint with `curl`, which bypasses the browser entirely and
  proves only that the server streams.

Neither touched the thing in between. So: parse the delivered script, and verify in a real browser
that it ran.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from nav_sentinel.webapp import pages


def _blocks(html: str) -> list[str]:
    return re.findall(r"<script>(.*?)</script>", html, re.DOTALL)


class TestTheDeliveredScriptParses:
    def test_no_string_literal_spans_a_line_break(self):
        """The general form of the bug, checkable without a JS engine.

        A quote opened and not closed on the same line means a literal newline landed inside a
        string -- which is exactly what a mishandled `\\n` escape produces, and it is a syntax
        error in JavaScript.
        """
        for block in _blocks(pages._WORK_SCRIPT):
            for number, line in enumerate(block.splitlines(), 1):
                stripped = re.sub(r"//.*$", "", line)
                for quote in ("'", '"'):
                    unescaped = len(re.findall(rf"(?<!\\){quote}", stripped))
                    assert unescaped % 2 == 0, (
                        f"line {number} opens a {quote} string and does not close it: {line!r}"
                    )

    def test_the_newline_escape_survives_as_two_characters(self):
        """Targeted at the exact defect: the JS must contain backslash-n, not a newline."""
        script = pages._WORK_SCRIPT
        assert "split('\\n')" in script, (
            "the newline escape was interpreted by Python instead of being passed to the browser"
        )
        assert "split('\n')" not in script

    @pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
    def test_node_can_parse_it(self, tmp_path):
        """The real check, when a JS engine is available."""
        for index, block in enumerate(_blocks(pages._WORK_SCRIPT)):
            path = tmp_path / f"block{index}.js"
            path.write_text(block)
            result = subprocess.run(
                ["node", "--check", str(path)], capture_output=True, text=True, check=False
            )
            assert result.returncode == 0, result.stderr


class TestTheScriptAnnouncesThatItRan:
    def test_it_marks_the_form_as_enhanced(self):
        """A marker in the DOM is the only thing that distinguishes "the script was served" from
        "the script ran". The first is what a page-content assertion can see; the second is what
        matters, and it is what a headless browser can be asked."""
        assert "form.dataset.enhanced = '1'" in pages._WORK_SCRIPT

    def test_the_fallback_form_still_posts_to_the_redirecting_route(self):
        """Whatever the script does, the form without it must reach a route that returns a page.

        Pointing the form itself at the streaming endpoint would have left a no-JavaScript browser
        staring at raw NDJSON.
        """
        from nav_sentinel.control_plane.approvals import Principal

        html = pages._actions(
            {"case_id": "CASE-X"},
            Principal(subject="a@b.example", role="controller"),
            "four_eyes",
            [],
            False,
        )
        action = re.search(r'<form[^>]*\saction="([^"]+)"', html).group(1)
        assert action.endswith("/work"), action
        assert not action.endswith("/work/stream")


HARNESS = Path(__file__).parent / "js" / "harness.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
class TestWhatTheClientDoesWithTheLinesItReceives:
    """The last unguarded layer.

    Python tests check what is served, `node --check` checks that it parses, and a headless browser
    proves it attaches. None of them exercises what the handler *does* with the stream -- which is
    the whole demo, and forty lines of code that has already been broken once.

    Driven through `tests/js/harness.js`, which answers exactly the DOM queries the script makes and
    delivers the NDJSON in two chunks **with a line split across the boundary** -- because that is
    what a real stream does, and a reader that assumes whole lines per chunk works until it does
    not.
    """

    @pytest.fixture(scope="class")
    def result(self, tmp_path_factory) -> dict:
        import json

        script = tmp_path_factory.mktemp("js") / "client.js"
        script.write_text(_blocks(pages._WORK_SCRIPT)[0])
        run = subprocess.run(
            ["node", str(HARNESS), str(script)], capture_output=True, text=True, check=False
        )
        assert run.returncode == 0, run.stderr
        return json.loads(run.stdout)

    def test_it_takes_over_the_submit_instead_of_letting_the_form_navigate(self, result):
        assert result["navigated"] is False
        assert result["enhanced"] == "1"
        assert result["disabled"] is True

    def test_every_stage_is_marked_running_then_done_in_order(self, result):
        marks = [(stage, state) for stage, state, _note in result["marks"]]
        # `triage running` twice: once client-side the moment the button is clicked, once when the
        # server confirms it. On a cold instance those are ten seconds apart.
        assert marks[0] == ("triage", "running")
        collapsed = [m for i, m in enumerate(marks) if i == 0 or m != marks[i - 1]]
        assert collapsed == [
            ("triage", "running"),
            ("triage", "done"),
            ("routing", "running"),
            ("routing", "done"),
            ("investigation", "running"),
            ("investigation", "done"),
            ("proposal", "running"),
            ("proposal", "done"),
        ], collapsed

    def test_each_section_is_appended_once_and_empty_ones_are_not(self, result):
        """Routing sends no HTML when it succeeds -- only a refusal has a panel -- so an empty
        string must not append a blank card."""
        assert result["appended"] == [
            "<div>TRIAGE</div>",
            "<div>CAUSE</div><div>EVIDENCE</div>",
            "<div>PROPOSAL</div>",
        ]

    def test_the_final_line_replaces_the_progress_rail_with_the_approval_panel(self, result):
        assert result["railSwapped"] == "<div>APPROVAL RAIL</div>"
        assert result["status"] == "complete"
        assert result["finishedClass"] is True
