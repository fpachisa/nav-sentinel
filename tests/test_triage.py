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
#: Ground truth for **every** case in the cycle, keyed by what identifies it. The two cash cases
#: had no entry, so two of seven were unscored -- and one of them was a confident wrong answer,
#: structurally invisible to an assertion that filtered to the keys it knew. "7 of 7" was really
#: "5 of 5, and two we did not look at".
EXPECTED = {
    "US0378331005": "nav.fx_rate",
    "GB00BN7SWP63": "nav.fx_rate",
    "US5949181045": "nav.corporate_action",
    "FR0000121014": "nav.settlement",
    "US7170811035": "nav.settlement",
    # The EUR cash balance: accounting carries a settlement entry the custodian does not.
    "cash:EUR": "nav.settlement",
    # The USD cash balance: a gross dividend against a net one, plus a failed settlement.
    "cash:USD": "nav.settlement",
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
    def test_the_vocabulary_is_the_owning_processs_capabilities(self):
        """Not a literal list, and not the whole fleet. Offering every registered capability let a
        NAV break be classified as another process's -- verified with a second pack registered, the
        schema accepted it, the floor kept it, and the NAV enum then raised."""
        allowed = triage.draft_model("nav").model_fields["capability"].annotation.__args__
        assert set(allowed) == {c for c in gateway.capabilities() if c.startswith("nav.")}

    def test_another_processs_capability_is_not_offered(self, monkeypatch):
        monkeypatch.setattr(
            gateway, "capabilities", lambda: ("nav.fx_rate", "nav.unclassified", "ta.subscription")
        )
        allowed = triage.draft_model("nav").model_fields["capability"].annotation.__args__
        assert "ta.subscription" not in allowed

    def test_an_out_of_vocabulary_answer_is_rejected_by_the_schema(self):
        from pydantic import ValidationError

        schema = triage.draft_model("nav")
        with pytest.raises(ValidationError):
            schema.model_validate({"capability": "nav.invented", "confidence": 0.9})

    def test_the_schema_is_built_per_call_so_a_new_process_changes_it(self, monkeypatch):
        """Frozen at import it would describe a fleet that no longer exists."""
        monkeypatch.setattr(gateway, "capabilities", lambda: ("nav.fx_rate", UNCLASSIFIED))
        allowed = triage.draft_model("nav").model_fields["capability"].annotation.__args__
        assert set(allowed) == {"nav.fx_rate", UNCLASSIFIED}

    def test_an_unknown_namespace_is_a_loud_failure(self):
        with pytest.raises(RuntimeError, match="under 'kyc'"):
            triage.draft_model("kyc")

    def test_no_capabilities_at_all_is_a_loud_failure(self, monkeypatch):
        monkeypatch.setattr(gateway, "capabilities", tuple)
        with pytest.raises(RuntimeError, match="nothing to classify into"):
            triage.draft_model("nav")

    def test_every_capability_triage_can_return_maps_to_a_break_category(self):
        """`ExceptionCase.category` is a closed enum, so an unmappable answer would crash the caller
        rather than route badly.

        Over the *NAV* vocabulary, which is what triage is offered for a NAV break -- the schema is
        scoped to the namespace of the process that detected it. Iterating the whole fleet's
        capabilities was only correct while the fleet was one process.
        """
        from nav_sentinel.agents.contract import category_for

        vocabulary = triage.draft_model("nav").model_fields["capability"].annotation.__args__
        assert vocabulary
        for capability in vocabulary:
            assert category_for(capability) is not None


class TestAWrongAnswerMustBeAdmitted:
    @staticmethod
    def _draft(capability: str, confidence: float):
        return triage.draft_model("nav").model_validate(
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
    """Asserted against the agent that gets built, not against this module's source text. Both
    source-grep versions were defeated by mutation: `model=settings().model_classify` kept the
    model test green while the agent stopped reading its manifest, and building the full
    investigative surface under an aliased import kept the tools test green while triage held four
    tools."""

    @staticmethod
    def _agent():
        manifest = discover.get("triage-agent")
        return manifest, triage.build_agent(manifest, _case(), triage.draft_model("nav"))

    def test_it_is_given_no_tools(self):
        """A triage agent holding investigative tools would begin the work its own routing decision
        is supposed to delegate."""
        _, agent = self._agent()
        assert asyncio.run(agent.canonical_tools()) == []
        assert agent.code_executor is None

    def test_its_model_comes_from_its_manifest(self):
        manifest, agent = self._agent()
        assert agent.model == manifest.model

    def test_a_manifest_declaring_another_model_is_honoured(self):
        """The property is provenance, not the literal string: a hardcoded model would satisfy an
        equality check against the manifest that happens to name the same one."""
        manifest = discover.get("triage-agent")
        renamed = manifest.model_copy(update={"model": "gemini-3.5-flash-lite-sentinel"})
        agent = triage.build_agent(renamed, _case(), triage.draft_model("nav"))
        assert agent.model == "gemini-3.5-flash-lite-sentinel"

    def test_its_output_is_schema_constrained_and_reaches_state(self):
        _, agent = self._agent()
        assert agent.output_schema is not None
        assert agent.output_key == "classification"


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
        assert unrouted == [
            "nav.cash_fees",
            "nav.pricing",
            "nav.unclassified",
            "ta.transfer_mismatch",
            "ta.unclassified",
        ], unrouted

    def test_an_unclassified_break_reaches_no_investigator(self):
        """Triage claimed `nav.unclassified`, so the confidence floor -- whose whole purpose is to
        send an uncertain break to a human -- routed it straight back to the classifier. Measured: a
        429 inside classify produced unclassified at 0.00, which was routed to triage-agent, which
        returned a verdict about the FX rate at confidence 1.00 in a green panel."""
        assert discover.discover_for_capability(UNCLASSIFIED) is None
        assert discover.get("triage-agent").handles_capabilities == ()

    def test_an_investigator_refuses_a_case_outside_its_declared_competence(self):
        """The structural half. Nothing bound a case to an agent's declared competence, so any
        manifest could be handed any case and the registry's decision was advisory."""
        import asyncio

        from nav_sentinel.agents import investigator

        case = _case()
        case.category = __import__(
            "nav_sentinel.domain.models", fromlist=["BreakCategory"]
        ).BreakCategory.FX_RATE
        with pytest.raises(investigator.NotAuthorisedForCapability, match="does not declare"):
            asyncio.run(investigator.investigate(case, discover.get("triage-agent")))


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

    def test_reading_the_books_for_a_signal_is_policed(self):
        """The reads went straight to `books_and_records`, so an agent scoped to securities and the
        registry was handed position data with **no policy decision recorded anywhere** -- and those
        facts go into a model's prompt. Computing something on an agent's behalf is still reading it
        on the agent's behalf."""
        from nav_sentinel.control_plane import identity

        gateway.clear_decision_log()
        with identity.acting_as("triage-agent"):
            signals.for_case(_named_case("US0378331005"))
        recorded = {d.policy_id for d in gateway.decision_log()}
        assert "P-001-TOOL-ALLOWLIST" in recorded
        assert "P-006-DATA-SCOPE" in recorded
        assert all(d.allowed for d in gateway.decision_log())

    def test_an_agent_without_the_scope_cannot_obtain_signals(self):
        from nav_sentinel.control_plane import identity
        from nav_sentinel.control_plane.policies import PolicyViolation

        with identity.acting_as("remediation-agent"):
            with pytest.raises(PolicyViolation, match="P-001|P-006"):
                signals.for_case(_named_case("US0378331005"))

    def test_signals_cannot_be_obtained_with_no_identity_bound(self):
        """`unbound()` rather than skipping the class fixture: the point is that dropping the
        binding is enough to lose access, not that the test happened to start without one."""
        from nav_sentinel.control_plane import identity
        from nav_sentinel.control_plane.identity import IdentityError

        case = _named_case("US0378331005")
        with identity.unbound(), pytest.raises(IdentityError):
            signals.for_case(case)

    def test_the_triage_manifest_declares_what_it_actually_reads(self):
        """A manifest that under-declares is a lie the gateway then enforces against the agent's own
        work: triage would have been denied its own signals."""
        manifest = discover.get("triage-agent")
        for tool in ("books_and_records.positions", "books_and_records.cash_movements"):
            assert tool in manifest.allowed_tools, tool
        for scope in ("positions", "cash_movements"):
            assert scope in manifest.data_scopes.read, scope

    def test_the_signals_are_stable_across_runs(self):
        """The prompt is part of a reproducible run: two identical cycles must produce identical
        instructions, or `make eval` cannot be compared against itself."""
        case = _named_case("US0378331005")
        assert signals.for_case(case) == signals.for_case(case)


def _key(case: ExceptionCase) -> str:
    """How a case is identified for scoring: its securities, or the currency of its cash break."""
    isins = sorted({b.isin for b in case.breaks if b.isin})
    if isins:
        return ",".join(isins)
    currencies = sorted({b.currency for b in case.breaks if b.currency})
    return f"cash:{','.join(currencies)}" if currencies else "cash"


def _named_case(isin: str) -> ExceptionCase:
    from nav_sentinel.pipeline import cycle_runner

    return next(c for c in cycle_runner.detect(AS_OF) if any(b.isin == isin for b in c.breaks))


@pytest.fixture(autouse=True)
def _as_triage():
    """Most of these read the books for signals, which is now policed -- so a bound identity is
    part of the setup rather than something the tests can do without."""
    from nav_sentinel.control_plane import identity

    with identity.acting_as("triage-agent"):
        yield


@pytest.mark.live
class TestTriageAgainstTheRealModel:
    """The accuracy half. Measured 20 August against `gemini-3.5-flash-lite`: 7 of 7 cases correct,
    no confident wrong answers. Before the deterministic signals were added it scored 2 of 6 with
    two confident wrong answers, which is what the signals exist for."""

    _cached: list[tuple[str, Classification]] = []

    @classmethod
    def _classify_all(cls) -> list[tuple[str, Classification]]:
        """One pass per session, and skip on quota rather than fail.

        Three tests each driving their own pass made 21 model calls; and being rate limited is not
        the classifier being wrong, so it must not read as it.
        """
        if cls._cached:
            return cls._cached

        from nav_sentinel.pipeline import cycle_runner

        manifest = discover.get("triage-agent")

        async def run():
            out = []
            for case in cycle_runner.detect(AS_OF):
                out.append((_key(case), await triage.classify(case, manifest)))
            return out

        try:
            cls._cached = asyncio.run(run())
        except Exception as exc:  # noqa: BLE001
            if "429" in str(exc) or "RESOURCE_EXHAUSTED" in str(exc).upper():
                pytest.skip(f"model quota exhausted, not a wrong answer: {str(exc)[:120]}")
            raise
        return cls._cached

    def test_every_case_in_the_cycle_has_ground_truth(self):
        """Otherwise an assertion that filters to known keys cannot see a wrong answer on the rest."""
        unscored = [k for k, _ in self._classify_all() if k not in EXPECTED]
        assert unscored == [], unscored

    def test_it_classifies_at_least_five_of_the_seven_cases(self):
        results = self._classify_all()
        correct = [k for k, c in results if c.capability == EXPECTED[k]]
        assert len(correct) >= 5, [
            (k, c.capability, EXPECTED[k]) for k, c in results if c.capability != EXPECTED[k]
        ]

    def test_it_never_returns_a_confident_wrong_category(self):
        """The criterion that matters more than accuracy."""
        wrong = [
            (k, c.capability, EXPECTED[k])
            for k, c in self._classify_all()
            if c.classified and c.capability != EXPECTED[k]
        ]
        assert wrong == [], wrong

    def test_every_classification_either_routes_or_escalates(self):
        for key, classification in self._classify_all():
            agent = discover.discover_for_capability(classification.capability)
            if agent is None:
                assert classification.capability in (
                    "nav.pricing", "nav.cash_fees", UNCLASSIFIED,
                ), key
            else:
                assert classification.capability in agent.handles_capabilities, key
