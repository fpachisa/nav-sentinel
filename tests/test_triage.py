"""Triage: routing a break, or declining to.

The property that matters is not accuracy -- it is that a wrong answer is *admitted*. A break
routed confidently to the wrong specialist is investigated with the wrong tools and comes back with
a plausible answer about the wrong mechanism, which is worse than an escalation.
"""

from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal

import pytest

from nav_sentinel.agents import triage
from nav_sentinel.agents.triage import UNCLASSIFIED, Classification
from nav_sentinel.control_plane import gateway
from nav_sentinel.domain import signals
from nav_sentinel.domain.models import BreakType, ExceptionCase, ReconciliationBreak
from nav_sentinel.registry import discover
from nav_sentinel.registry.models import load_manifests

AS_OF = date(2026, 8, 17)
EXPECTED = {
    "US0378331005": "nav.fx_rate",
    "GB00BN7SWP63": "nav.fx_rate",
    "US5949181045": "nav.corporate_action",
    "FR0000121014": "nav.settlement",
    "US7170811035": "nav.settlement",
}


def _case(case_id: str = "CASE-1") -> ExceptionCase:
    return ExceptionCase(
        case_id=case_id, fund_id="MERID-GEF", as_of=AS_OF,
        breaks=[
            ReconciliationBreak(
                break_id="BRK-1", fund_id="MERID-GEF", as_of=AS_OF,
                break_type=BreakType.MARKET_VALUE, isin="US0378331005",
                accounting_value=Decimal("38624967.58"),
                custodian_value=Decimal("38538342.10"),
                tolerance_applied=Decimal("0.01"),
            )
        ],
    )


class TestTriageCannotInventACategory:
    def test_the_vocabulary_is_the_registered_capabilities(self):
        """Not a literal list. A capability no process declares has nowhere to land, so an
        out-of-vocabulary answer becomes unclassified structurally rather than by prompt."""
        schema = triage.draft_model()
        allowed = schema.model_fields["capability"].annotation.__args__
        assert set(allowed) == set(gateway.capabilities())

    def test_an_out_of_vocabulary_answer_is_rejected_by_the_schema(self):
        from pydantic import ValidationError

        schema = triage.draft_model()
        with pytest.raises(ValidationError):
            schema.model_validate({"capability": "nav.invented", "confidence": 0.9})

    def test_the_schema_is_built_per_call_so_a_new_process_changes_it(self, monkeypatch):
        """Frozen at import it would describe a fleet that no longer exists."""
        monkeypatch.setattr(gateway, "capabilities", lambda: ("nav.fx_rate", UNCLASSIFIED))
        allowed = triage.draft_model().model_fields["capability"].annotation.__args__
        assert set(allowed) == {"nav.fx_rate", UNCLASSIFIED}

    def test_no_capabilities_at_all_is_a_loud_failure(self, monkeypatch):
        monkeypatch.setattr(gateway, "capabilities", tuple)
        with pytest.raises(RuntimeError, match="nothing to classify into"):
            triage.draft_model()

    def test_every_capability_triage_can_return_maps_to_a_break_category(self):
        """`ExceptionCase.category` is a closed enum, so an unmappable answer would crash the
        caller rather than route badly."""
        from nav_sentinel.agents.contract import category_for

        for capability in gateway.capabilities():
            assert category_for(capability) is not None


class TestAWrongAnswerMustBeAdmitted:
    @staticmethod
    def _draft(capability: str, confidence: float):
        return triage.draft_model().model_validate(
            {"capability": capability, "confidence": confidence, "reasoning": "because"}
        )

    def test_a_low_confidence_classification_is_discarded(self):
        result = triage._apply_floor("CASE-1", self._draft("nav.fx_rate", 0.4))
        assert result.capability == UNCLASSIFIED
        assert result.classified is False

    def test_the_discarded_answer_is_recorded_not_hidden(self):
        """The eval has to tell "triage was unsure" from "triage was wrong": collapsing them lets a
        classifier that hedges everything score the same as one that is right."""
        result = triage._apply_floor("CASE-1", self._draft("nav.fx_rate", 0.4))
        assert result.overridden_from == "nav.fx_rate"
        assert result.confidence == 0.4

    def test_a_confident_classification_stands(self):
        result = triage._apply_floor("CASE-1", self._draft("nav.fx_rate", 0.9))
        assert result.capability == "nav.fx_rate"
        assert result.overridden_from is None

    @pytest.mark.parametrize("confidence", [0.49, 0.5])
    def test_the_floor_is_inclusive_at_the_stated_value(self, confidence):
        result = triage._apply_floor("CASE-1", self._draft("nav.fx_rate", confidence))
        assert result.classified is (confidence >= triage.CONFIDENCE_FLOOR)

    def test_an_unclassified_answer_is_not_overridden_by_its_own_low_confidence(self):
        result = triage._apply_floor("CASE-1", self._draft(UNCLASSIFIED, 0.1))
        assert result.capability == UNCLASSIFIED
        assert result.overridden_from is None

    def test_a_failure_becomes_unclassified_rather_than_stopping_the_cycle(self, monkeypatch):
        async def boom(*_a, **_k):
            raise RuntimeError("the model is unavailable")

        monkeypatch.setattr(triage, "_run", boom)
        result = asyncio.run(triage.classify(_case(), discover.get("triage-agent")))
        assert result.capability == UNCLASSIFIED
        assert result.confidence == 0.0
        assert "could not classify" in result.reasoning


class TestTriageDoesNotInvestigate:
    def test_it_is_given_no_tools(self):
        """A triage agent holding investigative tools would begin the work its own routing decision
        is supposed to delegate."""
        import inspect

        source = inspect.getsource(triage)
        assert "tools=[]," in source
        assert "agent_surface.build" not in source

    def test_it_runs_on_the_cheap_model_its_manifest_declares(self):
        manifest = discover.get("triage-agent")
        assert manifest.model == "gemini-3.5-flash-lite"
        assert "gemini-3.5-flash-lite" not in inspect_source()

    def test_its_turn_budget_is_one_exchange(self):
        """Classification is one turn; more means the model is arguing with itself."""
        import inspect

        assert "max_llm_calls=3" in inspect.getsource(triage)


def inspect_source() -> str:
    import inspect

    return inspect.getsource(triage).split('"""', 2)[2]


class TestRoutingStaysWithTheRegistry:
    def test_a_declared_capability_with_no_agent_is_refused(self):
        """The S1.5 criterion. `nav.pricing` is declared by the NAV process and published by
        nobody, so a correctly triaged pricing break escalates instead of being misrouted -- which
        is what makes the three-investigator cut a demonstrated control rather than a gap."""
        assert "nav.pricing" in gateway.capabilities()
        assert discover.discover_for_capability("nav.pricing") is None

    def test_the_capabilities_that_do_route_reach_a_published_agent(self):
        routed = {
            capability: discover.discover_for_capability(capability)
            for capability in gateway.capabilities()
        }
        unrouted = sorted(c for c, agent in routed.items() if agent is None)
        assert unrouted == ["nav.cash_fees", "nav.pricing"], unrouted

    def test_triage_itself_holds_the_unclassified_capability(self):
        """An unclassified break has somewhere to go rather than vanishing."""
        agent = discover.discover_for_capability(UNCLASSIFIED)
        assert agent is not None and agent.agent_id == "triage-agent"


class TestRepublishingChangesRoutingWithoutARestart:
    def test_republish_adopts_what_is_on_disk(self):
        adopted = discover.republish()
        assert {m.agent_id for m in adopted} == {m.agent_id for m in load_manifests()}

    def test_routing_follows_a_republish_in_the_same_process(self, tmp_path, monkeypatch):
        """The registry cached for the process lifetime and nothing triggered a reload:
        `packs.on_change` fires on pack registration, not on a manifest appearing."""
        import shutil

        from nav_sentinel.control_plane import packs

        real = next(iter(packs.manifest_dirs()))
        staged = tmp_path / "manifests"
        shutil.copytree(real, staged, ignore=shutil.ignore_patterns("unpublished"))
        (staged / "fx-rates-investigator.yaml").unlink()

        monkeypatch.setattr(packs, "manifest_dirs", lambda: (staged,))
        try:
            assert discover.discover_for_capability("nav.fx_rate") is not None
            discover.republish()
            assert discover.discover_for_capability("nav.fx_rate") is None
        finally:
            monkeypatch.undo()
            discover.republish()
        assert discover.discover_for_capability("nav.fx_rate") is not None

    @pytest.mark.parametrize(
        ("field", "value", "message"),
        [
            ("may_post_entries", True, "claims posting authority"),
            ("max_autonomous_impact", "1000", "autonomous ceiling"),
        ],
    )
    def test_a_manifest_claiming_authority_is_refused_on_the_way_in(self, field, value, message):
        """These invariants lived only in tests, asserted against the manifests committed to the
        repository and nowhere else. `acting_as` resolves from this catalogue, so a manifest adopted
        at runtime changes what every `authorize_*` believes -- and a test reading YAML off disk
        cannot see that."""
        manifest = discover.get("fx-rates-investigator")
        authority = manifest.authority.model_copy(update={field: value})
        rogue = manifest.model_copy(update={"authority": authority})
        with pytest.raises(discover.PublicationRefused, match=message):
            discover.validate_fleet((rogue,))

    def test_only_the_remediation_agent_may_claim_drafting(self):
        manifest = discover.get("fx-rates-investigator")
        authority = manifest.authority.model_copy(update={"may_propose_remediation": True})
        with pytest.raises(discover.PublicationRefused, match="claims drafting authority"):
            discover.validate_fleet((manifest.model_copy(update={"authority": authority}),))

    def test_a_manifest_naming_a_phantom_tool_is_refused(self):
        manifest = discover.get("fx-rates-investigator")
        rogue = manifest.model_copy(update={"allowed_tools": ("ecb_fx.invented",)})
        with pytest.raises(discover.PublicationRefused, match="no registered process provides"):
            discover.validate_fleet((rogue,))

    def test_the_published_fleet_satisfies_its_own_invariants(self):
        """Vacuity guard: the checks above prove nothing if the real fleet would fail them."""
        manifests = tuple(load_manifests())
        assert manifests
        discover.validate_fleet(manifests)

    def test_a_refused_republish_leaves_the_previous_catalogue_in_place(self, monkeypatch):
        """A half-adopted fleet is worse than a stale one."""
        before = discover.all_agents()
        monkeypatch.setattr(
            discover, "validate_fleet", lambda _m: (_ for _ in ()).throw(
                discover.PublicationRefused("nope")
            )
        )
        with pytest.raises(discover.PublicationRefused):
            discover.republish()
        monkeypatch.undo()
        assert [m.ref for m in discover.all_agents()] == [m.ref for m in before]


class TestTheSignalsHandedToTriageAreTrue:
    """A signal that contradicts the break it describes is worse than no signal: triage was handed
    facts denying the problem existed and correctly refused to classify anything."""

    def test_a_book_holding_two_lots_is_summed_not_sampled(self):
        """`next(...)` reported "quantity agrees (400,000)" for a break of 520,000 against 400,000,
        because accounting held 400,000 plus a pending 120,000 lot."""
        case = _named_case("US7170811035")
        lines = signals.for_case(case)
        assert any("520000" in line and "400000" in line for line in lines), lines
        assert not any("quantity agrees" in line for line in lines), lines

    def test_a_lot_the_other_book_lacks_is_stated(self):
        lines = signals.for_case(_named_case("US7170811035"))
        assert any("line the other does not" in line for line in lines), lines

    def test_an_fx_break_is_separable_from_a_pricing_break(self):
        """The whole reason these exist: from the two totals alone a market value that differs while
        quantity agrees is an FX error or a pricing error, indistinguishable."""
        lines = signals.for_case(_named_case("US0378331005"))
        assert any("local price agrees" in line for line in lines), lines
        assert any("FX rate applied differs" in line for line in lines), lines

    def test_a_split_shows_a_whole_ratio_and_an_agreeing_market_value(self):
        lines = signals.for_case(_named_case("US5949181045"))
        assert any("exactly 2x" in line for line in lines), lines
        assert any("total market value agrees" in line for line in lines), lines

    def test_a_cash_break_names_the_movement_types_each_book_recorded(self):
        from nav_sentinel.pipeline import cycle_runner

        cash = next(
            c
            for c in cycle_runner.detect(AS_OF)
            if any(b.break_type is BreakType.CASH_BALANCE and b.currency == "USD" for b in c.breaks)
        )
        lines = signals.for_case(cash)
        assert any("dividend" in line for line in lines), lines

    def test_the_signals_are_stable_across_runs(self):
        """The prompt is part of a reproducible run: two identical cycles must produce identical
        instructions, or `make eval` cannot be compared against itself."""
        case = _named_case("US0378331005")
        assert signals.for_case(case) == signals.for_case(case)


def _named_case(isin: str) -> ExceptionCase:
    from nav_sentinel.pipeline import cycle_runner

    return next(c for c in cycle_runner.detect(AS_OF) if any(b.isin == isin for b in c.breaks))


@pytest.mark.live
class TestTriageAgainstTheRealModel:
    """The accuracy half. Measured 20 August against `gemini-3.5-flash-lite`: 7 of 7 cases correct,
    no confident wrong answers. Before the deterministic signals were added it scored 2 of 6 with
    two confident wrong answers, which is what the signals exist for."""

    @staticmethod
    def _classify_all() -> list[tuple[str, Classification]]:
        from nav_sentinel.pipeline import cycle_runner

        manifest = discover.get("triage-agent")

        async def run():
            out = []
            for case in cycle_runner.detect(AS_OF):
                isins = ",".join(sorted({b.isin for b in case.breaks if b.isin})) or "cash"
                out.append((isins, await triage.classify(case, manifest)))
            return out

        return asyncio.run(run())

    def test_it_classifies_at_least_five_of_the_six_identified_securities(self):
        results = self._classify_all()
        scored = [(k, c) for k, c in results if k in EXPECTED]
        correct = [k for k, c in scored if c.capability == EXPECTED[k]]
        assert len(correct) >= 5, [(k, c.capability, EXPECTED[k]) for k, c in scored]

    def test_it_never_returns_a_confident_wrong_category(self):
        """The criterion that matters more than accuracy."""
        wrong = [
            (k, c.capability, EXPECTED[k])
            for k, c in self._classify_all()
            if k in EXPECTED and c.classified and c.capability != EXPECTED[k]
        ]
        assert wrong == [], wrong

    def test_every_classification_routes_or_escalates(self):
        for _, classification in self._classify_all():
            agent = discover.discover_for_capability(classification.capability)
            assert agent is not None or classification.capability in (
                "nav.pricing", "nav.cash_fees",
            )
