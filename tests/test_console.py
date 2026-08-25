"""The console renders what the store holds, and escapes everything on the way out.

Escaping is a control here rather than hygiene. Observation summaries and verdict prose on the
corporate-action path derive from SEC filings -- attacker-authored text this system deliberately
ingests and screens with Model Armor. A screened payload that became markup in an operator's browser
would have defeated the screening by taking a different exit.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from nav_sentinel import composition, console
from nav_sentinel.control_plane.observations import Observation
from nav_sentinel.control_plane.repository import InMemoryRepository

CASE = "CASE-CONSOLE-1"
INJECTION = '<script>alert("pwned")</script>'


@pytest.fixture
def store() -> InMemoryRepository:
    return InMemoryRepository()


def _observation(summary: str, observed: dict[str, str]) -> Observation:
    return Observation(
        observation_id="OBS-console0000000",
        case_id=CASE,
        trace_id="d8bc651a64bdcd4eac21517327b02b85",
        agent_ref="corporate-actions-investigator@2.1.0",
        tool="edgar.filing_text",
        args="isin=US02319V1035",
        digest="0123456789abcdef0123",
        retrieved_at=datetime(2026, 8, 20, 9, 0, tzinfo=UTC),
        source="sec_edgar",
        source_uri="https://www.sec.gov/Archives/edgar/data/1",
        observed=observed,
        summary=summary,
    )


class TestEverythingFromOutsideIsEscaped:
    def test_a_script_tag_in_an_observation_does_not_become_markup(self, store):
        store.record_observation(_observation(INJECTION, {"ratio": INJECTION}))
        html = console.render(store, CASE, backend="memory")
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_a_script_tag_in_a_stage_note_does_not_become_markup(self, store):
        from nav_sentinel.control_plane import casefile
        from nav_sentinel.remediation_office.lifecycle import REMEDIATION

        casefile.open_case(store, CASE, REMEDIATION, note=INJECTION)
        html = console.render(store, CASE, backend="memory")
        assert "<script>" not in html

    def test_a_recorded_decision_actually_appears(self, store):
        """The screen was empty on a case with fourteen decisions, because it read the model's field
        names while the store keeps `as_span_attributes()`'s. A panel that shows nothing looks
        exactly like a system that decided nothing."""
        from nav_sentinel.control_plane.governance import Effect, PolicyDecision

        store.record_decision(
            CASE,
            "d8bc651a64bdcd4eac21517327b02b85",
            0,
            PolicyDecision(
                effect=Effect.DENY,
                policy_id="P-008-STAGE-TRANSITION",
                reason="compensation before approval is not a declared transition",
                resource=CASE,
            ),
        )
        html = console.render(store, CASE, backend="memory")
        assert "P-008-STAGE-TRANSITION" in html
        assert "compensation before approval" in html
        assert "DENY" in html
        assert "1</strong> of them refusals" in html or "<strong>1</strong>" in html

    def test_a_script_tag_in_a_denial_reason_does_not_become_markup(self, store):
        from nav_sentinel.control_plane.governance import Effect, PolicyDecision

        store.record_decision(
            CASE,
            None,
            0,
            PolicyDecision(
                effect=Effect.DENY,
                policy_id="P-001-TOOL-ALLOWLIST",
                reason=f"tool {INJECTION} is not allowed",
            ),
        )
        html = console.render(store, CASE, backend="memory")
        assert "<script>" not in html

    def test_attribute_context_is_escaped_too(self, store):
        """`html.escape(quote=True)`. A payload breaking out of an attribute needs no angle
        bracket, so escaping only `<` would leave the hole open."""
        store.record_observation(_observation('" onmouseover="x', {"ratio": '" autofocus'}))
        html = console.render(store, CASE, backend="memory")
        assert 'onmouseover="x' not in html


class TestThePageShowsWhatTheStoreHolds:
    def test_an_empty_case_says_so_rather_than_rendering_nothing(self, store):
        html = console.render(store, "CASE-NOTHING", backend="memory")
        assert "no stage history for this case" in html
        assert "no observations recorded" in html
        assert "no policy decisions recorded" in html

    def test_unrouted_capabilities_are_shown_as_gaps(self, store):
        composition.configure()
        html = console.render(store, CASE, backend="memory")
        assert "NONE" in html
        assert "rem.regulator_notification" in html

    def test_every_published_agent_appears(self, store):
        from nav_sentinel.registry import discover

        composition.configure()
        html = console.render(store, CASE, backend="memory")
        for manifest in discover.all_agents():
            assert manifest.agent_id in html, manifest.agent_id

    def test_posting_authority_would_be_flagged_if_any_agent_held_it(self):
        """Nothing in this fleet may post, so the branch that shows it is unreachable through the
        real registry. Called directly instead -- an unreachable branch in the column whose job is
        to make posting authority visible is a decorative column."""
        from nav_sentinel.registry.models import Authority

        assert "MAY POST" in console.authority_cell(Authority(may_post_entries=True))
        assert "may draft" in console.authority_cell(
            Authority(may_propose_remediation=True)
        )
        assert "reports only" in console.authority_cell(Authority())

    def test_the_real_fleet_holds_no_posting_authority(self, store):
        html = console.render(store, CASE, backend="memory")
        assert "MAY POST" not in html


class TestTheConsoleWritesNothing:
    def test_rendering_does_not_touch_the_store(self, store):
        from nav_sentinel.control_plane import casefile
        from nav_sentinel.remediation_office.lifecycle import REMEDIATION

        casefile.open_case(store, CASE, REMEDIATION)
        before = (
            len(store.stages_for(CASE)),
            len(store.observations_for(CASE)),
            len(store.decisions_for(CASE)),
        )
        console.render(store, CASE, backend="memory")
        assert (
            len(store.stages_for(CASE)),
            len(store.observations_for(CASE)),
            len(store.decisions_for(CASE)),
        ) == before

    def test_the_module_has_no_write_path(self):
        """Structural: the console must not be able to mutate anything, whatever a future edit
        intends. Asserted against the source so a write added later is visible here."""
        import inspect

        source = inspect.getsource(console)
        for forbidden in ("save_case", "record_decision", "record_observation", "record_stage",
                          "advance(", "open_case("):
            assert forbidden not in source, forbidden
