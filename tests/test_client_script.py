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

import pytest

from nav_sentinel.webapp import pages


def _blocks(html: str) -> list[str]:
    return re.findall(r"<script>(.*?)</script>", html, re.S)


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
