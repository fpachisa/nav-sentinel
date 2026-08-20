"""Prompt templates as files, and the ways that can go wrong.

The prose moved out of Python because it is the part of an agent that changes most and reads worst
inside a function -- three prompt defects in this project were found by measurement, and each meant
editing an f-string threaded through assembly logic.

Moving it out introduces a new failure the f-strings did not have: a placeholder that silently
renders blank. A prompt missing its evidence block took triage from 7-of-7 to 2-of-6, and it would
read as a model regression rather than a template one. So the tests here are mostly about that.
"""

from __future__ import annotations

from datetime import date

import pytest

from nav_sentinel.agents import investigator, prompts, remediation, triage
from nav_sentinel.agents.contract import Citation, Verdict
from nav_sentinel.control_plane import gateway, identity
from nav_sentinel.domain import materiality
from nav_sentinel.domain.models import BreakCategory
from nav_sentinel.pipeline import cycle_runner
from nav_sentinel.registry import discover
from nav_sentinel.tools import books_and_records as bnr

AS_OF = date(2026, 8, 17)
#: Agents that drive a model. The others are registry entries with no instruction of their own.
#:
#: Enumerated per process rather than as one hardcoded tuple, because the fund fleet's three were
#: hardcoded and the transfer-agency template was covered by nothing: deleting the `$required` line
#: from `register-investigator.md`, or all three of its "checked mechanically" clauses, left the
#: suite green. The live effect would be a model refused by P-007 for a rule its own instruction no
#: longer states -- the exact anti-pattern `TestTheTemplatesSayWhatTheCodeEnforces` exists to stop.
NAV_PROMPTED = ("triage-agent", "investigator", "remediation-agent")
TA_PROMPTED = ("register-investigator",)
PROMPTED = NAV_PROMPTED + TA_PROMPTED


@pytest.fixture
def case():
    scored = next(
        c for c in cycle_runner.detect(AS_OF) if any(b.isin == "US0378331005" for b in c.breaks)
    )
    materiality.score(
        scored,
        bnr.nav_record("custodian", "MERID-GEF", AS_OF),
        cycle_runner._fixture_rates(AS_OF),
    )
    scored.category = BreakCategory.FX_RATE
    return scored


@pytest.fixture
def verdict(case):
    return Verdict(
        case_id=case.case_id, capability="nav.fx_rate",
        root_cause="the stale 2026-08-14 USD rate of 1.1567 was applied",
        confidence=0.9, citations=[Citation(observation_id="OBS-x", relevance="r")],
    )


class TestTheTemplatesAreFoundThroughTheProcess:
    def test_every_prompted_agent_has_a_template(self):
        for agent_id in PROMPTED:
            assert prompts.path_for(agent_id).is_file()

    def test_the_directory_comes_from_the_registered_pack(self):
        """Declared by the pack, like the manifests, so a second process ships its own
        instructions without touching the loader."""
        directories = gateway.prompt_dirs()
        assert directories
        for directory in directories:
            assert directory.is_dir()

    def test_a_missing_template_is_a_loud_failure(self):
        with pytest.raises(prompts.PromptMissing, match="no prompt template"):
            prompts.path_for("agent-that-does-not-exist")

    def test_the_loader_does_not_import_the_pack_catalogue(self):
        """Reading the catalogue means holding the ungated tool callables, which is what the seam
        keeps away from `agents/` -- and the seam test caught the loader reaching for it directly."""
        import inspect

        source = inspect.getsource(prompts)
        assert "control_plane.packs" not in source
        assert "gateway.prompt_dirs" in source


class TestAPlaceholderCannotSilentlyRenderBlank:
    def test_an_unsupplied_placeholder_raises(self):
        """The failure a template file has and an f-string does not."""
        with pytest.raises(prompts.PromptIncomplete, match=r"\$display_name"):
            prompts.render("triage-agent", fund_id="MERID-GEF")

    def test_the_error_names_the_missing_placeholder_and_what_was_given(self):
        with pytest.raises(prompts.PromptIncomplete) as raised:
            prompts.render("investigator", display_name="x")
        assert "Given: ['display_name']" in str(raised.value)

    @pytest.mark.parametrize("agent_id", PROMPTED)
    def test_no_rendered_prompt_leaves_a_placeholder_behind(self, agent_id, case, verdict):
        rendered = _render(agent_id, case, verdict)
        assert "$" not in rendered, rendered[rendered.index("$") - 60 : rendered.index("$") + 20]

    @pytest.mark.parametrize("agent_id", PROMPTED)
    def test_every_placeholder_the_template_names_is_supplied(self, agent_id, case, verdict):
        """Keeps templates and callers in step: adding a placeholder to a file without supplying it
        fails here rather than at the model."""
        rendered = _render(agent_id, case, verdict)
        for name in prompts.placeholders(agent_id):
            assert f"${name}" not in rendered

    @pytest.mark.parametrize("agent_id", PROMPTED)
    def test_no_rendered_prompt_contains_an_empty_section(self, agent_id, case, verdict):
        """A blank line where a block should be is what a silently-missing substitution looks like."""
        rendered = _render(agent_id, case, verdict)
        assert "\n\n\n" not in rendered


def _render(agent_id: str, case, verdict) -> str:
    manifest = discover.get(agent_id if agent_id != "investigator" else "fx-rates-investigator")
    with identity.acting_as(manifest.agent_id):
        if agent_id == "triage-agent":
            return triage._instruction(manifest, case)
        if agent_id == "investigator":
            return investigator._instruction(manifest, case.to_brief())
        if agent_id == "register-investigator":
            # A transfer-agency brief, not a fund case. Feeding it the NAV fixture reached
            # `books_and_records` and tripped P-001 -- correctly, since this agent may not read the
            # fund's books, and that denial is itself the seam working.
            return investigator._instruction(manifest, _register_brief())
        return remediation._instruction(manifest, case, verdict)


def _nav_brief():
    scored = next(
        c for c in cycle_runner.detect(AS_OF) if any(b.isin == "US0378331005" for b in c.breaks)
    )
    materiality.score(
        scored,
        bnr.nav_record("custodian", "MERID-GEF", AS_OF),
        cycle_runner._fixture_rates(AS_OF),
    )
    scored.category = BreakCategory.FX_RATE
    return scored.to_brief()


def _register_brief():
    """A *classified* register case, as the cycle produces.

    `detect` leaves the capability at `ta.unclassified`, and an unclassified case has no declared
    evidence requirement -- so rendering from a raw detection made the instruction say "no particular
    facts" and a test asserting the requirement reached the model passed vacuously.
    """
    from nav_sentinel.transfer_agency import cycle, tolerance

    return cycle.classify(tolerance.detect("MERID-GEF", AS_OF)[0]).to_brief()


class TestTheComputedPartsStillReachTheModel:
    """What the templates cannot hold, and the reason a static instruction string would not do."""

    def test_triage_receives_the_deterministic_signals(self, case, verdict):
        """Given only the two disagreeing totals the model scored 2 of 6 with two confident wrong
        answers, and could not have done better."""
        rendered = _render("triage-agent", case, verdict)
        assert "local price agrees" in rendered
        assert "FX rate applied differs" in rendered

    def test_triage_receives_the_confidence_floor_from_code(self, case, verdict):
        assert f"Below {triage.CONFIDENCE_FLOOR} confidence" in _render(
            "triage-agent", case, verdict
        )

    def test_the_investigator_is_told_the_facts_the_process_requires(self, case, verdict):
        """Read from the pack, so a changed rule changes the instruction rather than leaving the
        model working to a stale one."""
        rendered = _render("investigator", case, verdict)
        for fact in gateway.evidence_requirement_for("nav.fx_rate"):
            assert fact in rendered

    def test_every_prompted_investigator_is_told_its_own_processs_required_facts(self):
        """Per capability, across processes, and asserted against the *pack* rather than against the
        template's own placeholders.

        The earlier version of this class iterated `prompts.placeholders(agent_id)` -- read from the
        template -- so deleting the `$required` line deleted its own check and left the suite green.
        The live consequence is a verdict refused by P-007 for a rule the model was never told, which
        reads as a model failure and is a prompt failure.
        """
        checked = 0
        for agent_id, capability in (
            ("investigator", "nav.fx_rate"),
            ("register-investigator", "ta.subscription_in_transit"),
        ):
            required = gateway.evidence_requirement_for(capability)
            assert required, f"{capability} declares no requirement, so this proves nothing"
            manifest = discover.get(
                agent_id if agent_id != "investigator" else "fx-rates-investigator"
            )
            with identity.acting_as(manifest.agent_id):
                brief = (
                    _register_brief()
                    if agent_id == "register-investigator"
                    else _nav_brief()
                )
                rendered = investigator._instruction(manifest, brief)
            for fact in required:
                assert fact in rendered, f"{agent_id} is not told it must cite {fact}"
            checked += 1
        assert checked == 2

    def test_the_drafting_agent_is_told_the_funds_base_currency(self, case, verdict):
        """Without it the model produced the right account and the right amount to the cent in the
        security's local trading currency."""
        assert "base currency EUR" in _render("remediation-agent", case, verdict)

    def test_the_drafting_agent_is_told_the_established_cause(self, case, verdict):
        assert "1.1567" in _render("remediation-agent", case, verdict)

    def test_a_local_currency_is_labelled_as_one(self, case, verdict):
        """The break line renders the security's local currency beside a base-currency difference.
        Labelling it plainly `currency` handed the model an EUR amount tagged USD, and the prompt
        then had to argue it back with a later sentence."""
        for agent_id in PROMPTED:
            rendered = _render(agent_id, case, verdict)
            if "USD" in rendered:
                assert "local currency USD" in rendered, agent_id


class TestTheTemplatesSayWhatTheCodeEnforces:
    """A rejection the model was never warned about is not correctable. A warning nothing enforces
    is worse -- the investigator prompt promised that naming an uncited value would be rejected, and
    nothing checked it for two commits."""

    def test_the_investigator_prompt_lists_each_mechanical_check(self, case, verdict):
        rendered = _render("investigator", case, verdict)
        assert "cites no observations" in rendered
        assert "cannot be found in the observations you cited" in rendered
        assert "do not between them carry" in rendered

    def test_the_drafting_prompt_states_the_per_currency_balance_rule(self, case, verdict):
        rendered = _render("remediation-agent", case, verdict)
        assert "within each currency" in rendered

    def test_the_drafting_prompt_offers_only_accounts_the_schema_accepts(self, case, verdict):
        rendered = _render("remediation-agent", case, verdict)
        for account in remediation.ACCOUNTS:
            assert account in rendered

    def test_the_drafting_prompt_does_not_ask_for_what_it_may_not_set(self, case, verdict):
        """The band and the residual are computed. A model asked for its own approval level would
        decide how many humans review its work."""
        rendered = _render("remediation-agent", case, verdict).lower()
        assert "not taken from you" in rendered

    def test_no_prompt_tells_an_agent_it_may_post(self, case, verdict):
        for agent_id in PROMPTED:
            rendered = _render(agent_id, case, verdict).lower()
            for forbidden in ("post the entry", "commit the entry", "update the ledger"):
                assert forbidden not in rendered, agent_id


class TestASecondProcessWouldShipItsOwn:
    def test_the_pack_declares_where_its_prompts_live(self):
        from nav_sentinel.control_plane import packs

        pack = packs.registered()[0]
        resolved = pack.prompt_dir or pack.manifest_dir.parent / "prompts"
        assert resolved in gateway.prompt_dirs()

    def test_the_loader_searches_every_registered_process(self):
        import inspect

        assert "for directory in gateway.prompt_dirs()" in inspect.getsource(prompts.path_for)

    def test_templates_are_packaged_with_the_process(self):
        """They ship inside the package rather than beside the repo, or a deployed image would have
        the manifests and none of the instructions.

        Per process: each template must live under *its own* package, which is the property that
        makes a second process able to ship instructions at all. Asserting one hardcoded directory
        for every agent is what kept the transfer-agency template out of this class.
        """
        for agent_id in NAV_PROMPTED:
            assert "src/nav_sentinel/domain/prompts" in str(prompts.path_for(agent_id))
        for agent_id in TA_PROMPTED:
            assert "src/nav_sentinel/transfer_agency/prompts" in str(prompts.path_for(agent_id))
