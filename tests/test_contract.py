"""The investigator contract: what a verdict may say, and what it may cite.

Two properties carry the weight here. A verdict asserting a cause must cite an observation the
platform recorded *for that case*, and a model may not supply any field a downstream check tests.
Both exist because a model will happily produce well-shaped citations for a rate it never fetched.
"""

from __future__ import annotations

import inspect
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from nav_sentinel.agents import contract
from nav_sentinel.agents.contract import (
    UNKNOWN,
    Citation,
    Observation,
    UnknownObservation,
    Verdict,
)
from nav_sentinel.control_plane import observations
from nav_sentinel.domain.models import BreakCategory, ObservedFacts

CASE = "CASE-MERID-GEF-2026-08-17-0001"


def _observation(case_id: str = CASE, obs_id: str = "OBS-aaaa000000000000") -> Observation:
    return Observation(
        observation_id=obs_id,
        case_id=case_id,
        trace_id="d8bc651a64bdcd4eac21517327b02b85",
        agent_ref="fx-rates-investigator@1.3.0",
        tool="ecb_fx.latest_rate_on_or_before",
        args="currency=USD,day=2026-08-17",
        digest="0123456789abcdef",
        retrieved_at=datetime(2026, 8, 20, 9, 0, tzinfo=UTC),
        source="ecb_fx_reference_rates",
        source_uri="https://data-api.ecb.europa.eu/service/data/EXR",
        # Stored as text, because the platform records observations without knowing what a
        # `rate_date` is -- the process projects and rebuilds them. See `control_plane.observations`.
        observed=observations.stringify(
            {"rate": Decimal("1.1567"), "rate_date": date(2026, 8, 14)}
        ),
        summary="USD reference rate 1.1567 published 2026-08-14",
    )


def _verdict(**overrides) -> Verdict:
    kwargs = {
        "case_id": CASE,
        "capability": "nav.fx_rate",
        "root_cause": "Accounting applied the 14 August rate to a 17 August valuation",
        "confidence": 0.86,
        "citations": [Citation(observation_id="OBS-aaaa000000000000", relevance="the rate used")],
    }
    return Verdict(**{**kwargs, **overrides})


class TestAVerdictCannotAssertWithoutEvidence:
    def test_an_asserted_cause_with_no_citation_is_refused(self):
        with pytest.raises(ValidationError, match="must cite at least one observation"):
            _verdict(citations=[])

    def test_a_refusal_needs_no_citation(self):
        """Scoped deliberately: a refusal asserts nothing, so requiring evidence of it would make
        the refusal path unrepresentable -- and that path is the designed outcome of the poisoned
        corporate-action notice."""
        verdict = _verdict(root_cause=UNKNOWN, confidence=0.0, citations=[])
        assert verdict.asserts_a_cause is False

    def test_an_unknown_cause_cannot_be_held_confidently(self):
        with pytest.raises(ValidationError, match="cannot be held confidently"):
            _verdict(root_cause=UNKNOWN, confidence=0.9, citations=[])

    @pytest.mark.parametrize("confidence", [-0.1, 1.1])
    def test_confidence_stays_a_probability(self, confidence):
        with pytest.raises(ValidationError):
            _verdict(confidence=confidence)


class TestAnInvestigatorCannotProposeACorrection:
    """P-002 in the type system as well as in a policy check."""

    def test_verdict_has_no_field_that_could_carry_a_proposal(self):
        forbidden = {"proposal", "lines", "journal_entry", "correction", "entries", "remediation"}
        assert forbidden.isdisjoint(Verdict.model_fields)

    def test_a_structured_proposal_cannot_be_smuggled_in_as_an_extra_field(self):
        with pytest.raises(ValidationError):
            Verdict(
                case_id=CASE, capability="nav.fx_rate", root_cause=UNKNOWN, confidence=0.0,
                proposal={"lines": [{"account": "cash", "debit": "1234"}]},
            )

    def test_the_prose_fields_are_not_claimed_to_be_safe(self):
        """The honest scope of the claim. `reasoning` is free text and a model can write a journal
        entry into it; what is guaranteed is that nothing *structured* crosses, and that S4 reads
        only the typed fields. This test pins the docstring to that narrower claim so it cannot
        drift back to "an investigator physically cannot propose a correction"."""
        doc = " ".join((contract.__doc__ or "").split())
        assert "are free text" in doc
        assert "reads only the typed fields" in doc


class TestACitationIsBoundToAnObservedFact:
    def test_an_uncited_observation_is_not_required_but_an_invented_one_is_refused(self):
        with pytest.raises(UnknownObservation, match="never\n?\\s*recorded|never recorded"):
            contract.resolve_citations(_verdict(), {})

    def test_an_observation_recorded_for_another_case_is_not_evidence_here(self):
        """The hole this mechanism exists to close. A real tool call, genuinely made, cited on a
        case it was not made for -- so the call vouches for an unrelated claim."""
        other = _observation(case_id="CASE-MERID-GEF-2026-08-17-0009")
        with pytest.raises(UnknownObservation, match="recorded on case"):
            contract.resolve_citations(_verdict(), {other.observation_id: other})

    def test_a_matching_observation_resolves(self):
        obs = _observation()
        assert contract.resolve_citations(_verdict(), {obs.observation_id: obs}) == [obs]

    def test_the_model_cannot_supply_anything_a_check_tests(self):
        """A citation carries an id and a sentence. Everything else is looked up."""
        assert set(Citation.model_fields) == {"observation_id", "relevance"}
        with pytest.raises(ValidationError):
            Citation(
                observation_id="OBS-x", relevance="r",
                source_uri="https://attacker.example/invented",
            )

    def test_evidence_is_built_from_the_observation_not_the_citation(self):
        obs = _observation()
        item = contract.evidence_from(obs, Citation(observation_id=obs.observation_id,
                                                   relevance="why it matters"))
        assert item.source_uri == obs.source_uri
        assert item.retrieved_at == obs.retrieved_at
        assert item.observed == ObservedFacts.from_recorded(obs.observed)
        assert item.summary == "why it matters"   # the only thing the model contributed


class TestTheRateDateSurvivesAsAFactRatherThanProse:
    """The S1 criterion is that an FX verdict cites the rate *and the rate date used*. Expressed
    as prose it could only be checked with a regex over model output, which is the hallucination
    hole the citation mechanism exists to close."""

    def test_the_observed_facts_use_the_goldens_vocabulary(self):
        """`evidence_must_cite: [rate, rate_date]` in the golden must be checkable by name."""
        assert {"rate", "rate_date", "gross_rate"} <= set(ObservedFacts.model_fields)

    def test_a_verdicts_cited_facts_include_the_rate_and_its_date(self):
        obs = _observation()
        hypothesis = _verdict().to_hypothesis({obs.observation_id: obs},
                                              agent_ref="fx-rates-investigator@1.3.0")
        cited = frozenset().union(*(e.observed.cited() for e in hypothesis.evidence))
        assert {"rate", "rate_date"} <= cited

    def test_observed_facts_are_frozen(self):
        facts = ObservedFacts(rate=Decimal("1.1567"))
        with pytest.raises(ValidationError):
            facts.rate = Decimal("9.9999")


class TestConversionToTheDomainType:
    def test_it_produces_the_hypothesis_the_case_already_holds(self):
        obs = _observation()
        hypothesis = _verdict().to_hypothesis({obs.observation_id: obs},
                                              agent_ref="fx-rates-investigator@1.3.0")
        assert hypothesis.category is BreakCategory.FX_RATE
        assert hypothesis.investigator_agent == "fx-rates-investigator"
        assert hypothesis.investigator_version == "1.3.0"
        assert len(hypothesis.evidence) == 1

    @pytest.mark.parametrize(
        ("capability", "expected"),
        [
            ("nav.fx_rate", BreakCategory.FX_RATE),
            ("nav.corporate_action", BreakCategory.CORPORATE_ACTION),
            ("nav.unclassified", BreakCategory.UNCLASSIFIED),
            ("ta.fx_rate", BreakCategory.FX_RATE),   # a second process's namespace
        ],
    )
    def test_capability_maps_to_a_category(self, capability, expected):
        assert contract.category_for(capability) is expected

    def test_an_unrecognised_capability_is_refused_rather_than_defaulted(self):
        """Defaulting to UNCLASSIFIED would turn a routing bug into a silently mis-filed case."""
        with pytest.raises(ValueError, match="does not name a break category"):
            contract.category_for("nav.invented_category")

    def test_every_capability_the_pack_declares_maps(self):
        """The conversion has to be total, or a declared capability has nowhere to land."""
        from nav_sentinel.control_plane import packs

        for capability in packs.capabilities():
            assert isinstance(contract.category_for(capability), BreakCategory)


class TestIdsAreDerivedNotCounted:
    """S8a requires a byte-identical re-run, which `itertools.count` cannot give."""

    def test_the_same_call_yields_the_same_id(self):
        args = ("CASE-1", "ecb_fx.rate_on", "currency=USD", "abc123")
        assert observations.observation_id(*args) == observations.observation_id(*args)

    def test_different_calls_yield_different_ids(self):
        a = observations.observation_id("CASE-1", "ecb_fx.rate_on", "currency=USD", "abc")
        b = observations.observation_id("CASE-1", "ecb_fx.rate_on", "currency=GBP", "abc")
        assert a != b

    def test_the_same_case_and_tool_with_a_different_result_is_a_different_observation(self):
        a = observations.observation_id("CASE-1", "ecb_fx.rate_on", "currency=USD", "digest-one")
        b = observations.observation_id("CASE-1", "ecb_fx.rate_on", "currency=USD", "digest-two")
        assert a != b

    @pytest.mark.parametrize(
        "value",
        [Decimal("1.1567"), date(2026, 8, 14), (date(2026, 8, 14), Decimal("1.1567")),
         {"rate": Decimal("1.1567")}, [1, 2, 3], "text", None],
    )
    def test_digests_are_stable_for_the_types_tools_return(self, value):
        assert observations.digest_of(value) == observations.digest_of(value)

    def test_a_decimal_is_digested_by_value_not_by_repr(self):
        """`repr(Decimal("1.1567"))` is `Decimal('1.1567')` -- a form that carries the class name
        and could move with a library version, breaking reproducibility silently. The digest is
        taken over the decimal's own text instead.

        A `Decimal` and the *string* "1.1567" still digest differently, and should: they are
        different values, and collapsing them would let a model's text match a numeric fact.
        """
        assert observations.digest_of(Decimal("1.1567")) != observations.digest_of(
            repr(Decimal("1.1567"))
        )
        assert observations.digest_of(Decimal("1.1567")) != observations.digest_of("1.1567")
        assert observations.digest_of(Decimal("1.1567")) == observations.digest_of(Decimal("1.1567"))


class TestTheRefusalHelper:
    def test_it_carries_the_real_reason_rather_than_a_fixed_label(self):
        """An earlier draft hardcoded a P-005 label, which would describe an allowlist denial as a
        screening block."""
        source = inspect.getsource(contract.refusal)
        assert "P-005" not in source.split('"""')[2], "a policy id is hardcoded in the body"
        verdict = contract.refusal(CASE, "nav.corporate_action", reason="P-001 denied edgar.fetch")
        assert "P-001" in verdict.unresolved

    def test_a_refusal_with_an_observation_cites_it(self):
        obs = _observation()
        verdict = contract.refusal(CASE, "nav.fx_rate", reason="blocked", evidence=obs)
        assert verdict.citations[0].observation_id == obs.observation_id
        assert verdict.asserts_a_cause is False

    def test_recorded_times_are_timezone_aware(self):
        assert contract.utcnow().tzinfo is not None
