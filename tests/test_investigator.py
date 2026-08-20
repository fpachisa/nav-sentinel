"""The investigator: what it does with what a model returns.

The model runs behind the `live` marker. Everything that decides whether a verdict is *accepted*
runs offline, against hand-built drafts, because that is the security-relevant half and a flaky
model must never be able to fail the offline gate.
"""

from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal

import pytest

from nav_sentinel.agents import investigator
from nav_sentinel.agents.contract import UNKNOWN, Verdict
from nav_sentinel.agents.investigator import VerdictDraft
from nav_sentinel.control_plane import agent_surface, gateway, identity, packs
from nav_sentinel.control_plane.observations import ObservationStore
from nav_sentinel.domain.models import (
    BreakCategory,
    BreakType,
    ExceptionCase,
    ReconciliationBreak,
)
from nav_sentinel.registry import discover

FUND = "MERID-GEF"
AS_OF = date(2026, 8, 17)


def _case(case_id: str = "CASE-1", category: BreakCategory = BreakCategory.FX_RATE) -> ExceptionCase:
    return ExceptionCase(
        case_id=case_id,
        fund_id=FUND,
        as_of=AS_OF,
        category=category,
        breaks=[
            ReconciliationBreak(
                break_id="BRK-1", fund_id=FUND, as_of=AS_OF,
                break_type=BreakType.MARKET_VALUE, isin="US0378331005",
                accounting_value=Decimal("38624967.58"),
                custodian_value=Decimal("38538342.10"),
                tolerance_applied=Decimal("0.01"),
            )
        ],
    )


@pytest.fixture
def fx_manifest():
    return discover.get("fx-rates-investigator")


@pytest.fixture
def store_with_a_rate(fx_manifest):
    """A store holding one genuine ECB observation for CASE-1, from the offline cassette."""
    store = ObservationStore()
    with identity.acting_as(fx_manifest.agent_id):
        tools = {
            t.nav_tool_name: t
            for t in agent_surface.build(
                fx_manifest, case_id="CASE-1", trace_id="tr", store=store
            )
        }
        returned = tools["ecb_fx.latest_rate_on_or_before"](currency="USD", day="2026-08-17")
    return store, returned["observation_id"]


class TestTheAgentIsBuiltFromItsManifest:
    def test_the_model_comes_from_the_manifest_not_a_literal(self, fx_manifest):
        """`data_scopes` and `max_autonomous_impact` were both declared and read by nothing before.
        A hardcoded model would put `model:` in the same category."""
        import inspect

        source = inspect.getsource(investigator)
        assert "manifest.model" in source
        for literal in ("gemini-3.7-flash", "gemini-3.5-flash-lite", "gemini-2"):
            assert f'"{literal}"' not in source, f"{literal} is hardcoded"

    def test_the_adk_name_is_derived_and_the_ref_is_not(self, fx_manifest):
        """ADK rejects a hyphenated name outright, so the two must be allowed to differ -- but the
        audit trail records the registry ref, so they cannot disagree about who acted."""
        assert investigator.adk_name("fx-rates-investigator") == "fx_rates_investigator"
        assert "-" in fx_manifest.ref

    def test_every_published_agent_id_survives_the_adk_name_rule(self):
        from google.adk.agents import Agent

        from nav_sentinel.registry.models import load_manifests

        manifests = load_manifests()
        assert manifests, "no manifests loaded; the test would prove nothing"
        for manifest in manifests:
            Agent(name=investigator.adk_name(manifest.agent_id), model=manifest.model)

    def test_the_prompt_names_the_case_and_its_breaks(self, fx_manifest):
        prompt = investigator._instruction(fx_manifest, _case())
        assert "CASE-1" in prompt and "2026-08-17" in prompt
        assert "US0378331005" in prompt
        assert "38624967.58" in prompt

    def test_the_prompt_states_the_two_rejection_rules(self, fx_manifest):
        """A rejection the model was never warned about is not correctable."""
        prompt = investigator._instruction(fx_manifest, _case())
        assert "observation_id" in prompt
        assert "without citing" in prompt
        assert UNKNOWN in prompt

    def test_the_prompt_does_not_tell_the_agent_it_may_fix_anything(self, fx_manifest):
        """No investigator may draft or post, so the prompt must not imply otherwise."""
        prompt = investigator._instruction(fx_manifest, _case()).lower()
        assert "you do not fix anything" in prompt
        for forbidden in ("post the", "journal entry", "correct the books"):
            assert forbidden not in prompt


class TestTheSchemaHandedToTheModelIsPermissive:
    """ADK validates `output_schema` inside the runner, so a cross-field rule raises there --
    unrecoverable, and surfacing as a framework error rather than a bad answer."""

    def test_a_draft_asserting_a_cause_with_no_citations_is_accepted_by_the_schema(self):
        draft = VerdictDraft(root_cause="Stale rate", confidence=0.9, observation_ids=[])
        assert draft.root_cause == "Stale rate"

    def test_the_strict_type_rejects_the_same_shape(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            Verdict(
                case_id="CASE-1", capability="nav.fx_rate",
                root_cause="Stale rate", confidence=0.9, citations=[],
            )

    def test_an_empty_reply_yields_a_draft_rather_than_an_exception(self):
        assert VerdictDraft().root_cause == UNKNOWN

    def test_unknown_keys_from_the_model_are_ignored_not_fatal(self):
        draft = VerdictDraft.model_validate(
            {"root_cause": "x", "confidence": 0.5, "observation_ids": [], "invented": 1}
        )
        assert not hasattr(draft, "invented")


class TestWhatIsDoneWithTheModelsDraft:
    def test_a_cited_cause_becomes_a_verdict(self, store_with_a_rate):
        store, observation_id = store_with_a_rate
        with identity.acting_as("fx-rates-investigator"):
            verdict = investigator._finalise(
                VerdictDraft(
                    root_cause="Accounting applied the 14 August rate to a 17 August valuation",
                    confidence=0.9,
                    observation_ids=[observation_id],
                ),
                _case(), "nav.fx_rate", store,
            )
        assert verdict.asserts_a_cause
        assert verdict.citations[0].observation_id == observation_id

    def test_the_relevance_note_is_derived_from_the_observation(self, store_with_a_rate):
        """Not from model text: the fields a downstream check reads must not come from the model."""
        store, observation_id = store_with_a_rate
        with identity.acting_as("fx-rates-investigator"):
            verdict = investigator._finalise(
                VerdictDraft(root_cause="x", confidence=0.9, observation_ids=[observation_id]),
                _case(), "nav.fx_rate", store,
            )
        note = verdict.citations[0].relevance
        assert "ecb_fx" in note and "rate=" in note

    def test_a_cause_with_no_citations_becomes_a_refusal_not_an_error(self, store_with_a_rate):
        store, _ = store_with_a_rate
        with identity.acting_as("fx-rates-investigator"):
            verdict = investigator._finalise(
                VerdictDraft(root_cause="Stale rate", confidence=0.95, observation_ids=[]),
                _case(), "nav.fx_rate", store,
            )
        assert verdict.root_cause == UNKNOWN
        assert verdict.confidence == 0.0
        assert "without citing" in verdict.unresolved

    def test_an_invented_observation_id_is_refused(self, store_with_a_rate):
        from nav_sentinel.agents.contract import UnknownObservation

        store, _ = store_with_a_rate
        with identity.acting_as("fx-rates-investigator"):
            with pytest.raises(UnknownObservation, match="never recorded"):
                investigator._finalise(
                    VerdictDraft(
                        root_cause="Stale rate", confidence=0.9,
                        observation_ids=["OBS-0000000000000000"],
                    ),
                    _case(), "nav.fx_rate", store,
                )

    def test_a_cause_the_evidence_cannot_corroborate_is_refused_by_p007(self, fx_manifest):
        """The critical case: a call that returned nothing carries no facts, so it cannot support
        an assertion however genuinely it was made."""
        from nav_sentinel.control_plane.policies import PolicyViolation

        store = ObservationStore()
        with identity.acting_as(fx_manifest.agent_id):
            tools = {
                t.nav_tool_name: t
                for t in agent_surface.build(
                    fx_manifest, case_id="CASE-1", trace_id=None, store=store
                )
            }
            empty = tools["ecb_fx.latest_rate_on_or_before"](currency="GBP", day="2026-07-01")
            assert empty["result"] is None
            with pytest.raises(PolicyViolation, match="P-007"):
                investigator._finalise(
                    VerdictDraft(
                        root_cause="Accounting applied a stale rate of 1.1567",
                        confidence=0.95,
                        observation_ids=[empty["observation_id"]],
                    ),
                    _case(), "nav.fx_rate", store,
                )

    def test_duplicate_citations_are_collapsed(self, store_with_a_rate):
        store, observation_id = store_with_a_rate
        with identity.acting_as("fx-rates-investigator"):
            verdict = investigator._finalise(
                VerdictDraft(
                    root_cause="x", confidence=0.8,
                    observation_ids=[observation_id, observation_id, observation_id],
                ),
                _case(), "nav.fx_rate", store,
            )
        assert len(verdict.citations) == 1

    def test_an_out_of_range_confidence_is_clamped_not_rejected(self, store_with_a_rate):
        """A model returning 1.4 has misread the scale, not produced an unusable answer."""
        store, observation_id = store_with_a_rate
        with identity.acting_as("fx-rates-investigator"):
            verdict = investigator._finalise(
                VerdictDraft(root_cause="x", confidence=1.4, observation_ids=[observation_id]),
                _case(), "nav.fx_rate", store,
            )
        assert verdict.confidence == 1.0


class TestRefusalIsAVerdictNotAnException:
    def test_evidence_failures_are_caught_and_a_governance_denial_is_not(self):
        """The distinction that matters. `PolicyViolation` is raised for P-001 through P-007, so
        catching it would render "this agent was denied a tool it must never call" as ordinary
        uncertainty -- and the poisoned notice's injected instruction is to post without review."""
        from nav_sentinel.control_plane.policies import PolicyViolation

        assert PolicyViolation not in investigator.EVIDENCE_FAILURES
        for failure in investigator.EVIDENCE_FAILURES:
            assert not issubclass(PolicyViolation, failure), (
                f"PolicyViolation is a subclass of {failure.__name__}, so catching evidence "
                f"failures would swallow every governance denial"
            )

    def test_a_tool_failure_reaches_the_agent_as_a_translated_type(self):
        """An agent importing `nav_sentinel.tools.ecb_fx` for its exception type would be handed
        the ungated callables in that module, which the seam forbids -- so the gateway translates."""
        assert gateway.ToolFailed in investigator.EVIDENCE_FAILURES

    def test_the_gateway_translates_a_tool_error(self, monkeypatch):
        spec = packs.catalogue()["ecb_fx.rate_on"]
        exploding = dict(packs.catalogue())
        exploding["ecb_fx.rate_on"] = packs.ToolSpec(
            name=spec.name, fn=lambda **_kw: 1 / 0, reads=spec.reads,
            description=spec.description, source=spec.source,
        )
        monkeypatch.setattr(packs, "catalogue", lambda: exploding)
        with identity.acting_as("fx-rates-investigator"):
            with pytest.raises(gateway.ToolFailed) as raised:
                gateway.call_tool("ecb_fx.rate_on", currency="USD", day=date(2026, 8, 17))
        assert isinstance(raised.value.cause, ZeroDivisionError)
        assert raised.value.tool_name == "ecb_fx.rate_on"

    def test_a_policy_denial_from_inside_a_tool_is_not_reclassified(self, monkeypatch):
        """Otherwise a denial raised deeper in the stack would arrive as an evidence failure and be
        softened into a low-confidence verdict."""
        from nav_sentinel.control_plane.policies import Effect, PolicyDecision, PolicyViolation

        def denier(**_kw):
            raise PolicyViolation(
                PolicyDecision(
                    effect=Effect.DENY, policy_id="P-001-TOOL-ALLOWLIST",
                    reason="denied", agent_ref="x", resource="y",
                )
            )

        spec = packs.catalogue()["ecb_fx.rate_on"]
        rigged = dict(packs.catalogue())
        rigged["ecb_fx.rate_on"] = packs.ToolSpec(
            name=spec.name, fn=denier, reads=spec.reads,
            description=spec.description, source=spec.source,
        )
        monkeypatch.setattr(packs, "catalogue", lambda: rigged)
        with identity.acting_as("fx-rates-investigator"):
            with pytest.raises(PolicyViolation):
                gateway.call_tool("ecb_fx.rate_on", currency="USD", day=date(2026, 8, 17))


class TestTheVerdictReachesTheDomain:
    def test_it_converts_to_the_hypothesis_the_case_holds(self, store_with_a_rate):
        store, observation_id = store_with_a_rate
        with identity.acting_as("fx-rates-investigator"):
            verdict = investigator._finalise(
                VerdictDraft(root_cause="Stale rate applied", confidence=0.9,
                             observation_ids=[observation_id]),
                _case(), "nav.fx_rate", store,
            )
        hypothesis = verdict.to_hypothesis(store.as_mapping(), agent_ref="fx-rates-investigator@1.3.0")
        assert hypothesis.category is BreakCategory.FX_RATE
        item = hypothesis.evidence[0]
        assert item.source_uri and item.retrieved_at, "the S1 criterion is not met"
        assert {"rate", "rate_date"} <= item.observed.cited()


#: One investigation per scenario for the whole session. Three tests each driving their own run
#: exhausted the per-minute quota and failed with 429 -- which is not the model being wrong, and
#: must not read as it.
_LIVE_RUNS: dict[str, tuple] = {}


def _investigate_live(isin: str):
    """Run the real model once per scenario, and skip rather than fail on a quota error."""
    if isin in _LIVE_RUNS:
        return _LIVE_RUNS[isin]

    from nav_sentinel.pipeline import cycle_runner

    case = next(c for c in cycle_runner.detect(AS_OF) if any(b.isin == isin for b in c.breaks))
    case.category = BreakCategory.FX_RATE
    try:
        result = asyncio.run(
            investigator.investigate(case, discover.get("fx-rates-investigator"))
        )
    except Exception as exc:  # noqa: BLE001
        if "429" in str(exc) or "RESOURCE_EXHAUSTED" in str(exc).upper():
            pytest.skip(f"model quota exhausted, not a wrong answer: {str(exc)[:120]}")
        raise
    _LIVE_RUNS[isin] = result
    return result


@pytest.mark.live
class TestTheFxInvestigatorAgainstTheRealModel:
    """The accuracy half, which needs a live model. Shape is tested offline above.

    Measured on 20 August 2026 against `gemini-3.7-flash`: the stale-rate case was diagnosed as
    "applied the stale 2026-08-14 rate of 1.1567 to the 2026-08-17 valuation, where the ECB
    reference rate was 1.1593", in six tool calls, citing both rates with their dates. The golden
    states the same cause independently.
    """

    def test_it_identifies_the_stale_rate_and_cites_both_rates(self):
        verdict, store = _investigate_live("US0378331005")
        assert verdict.asserts_a_cause, verdict.unresolved
        assert "1.1567" in verdict.root_cause and "1.1593" in verdict.root_cause
        dates = {
            store.get(c.observation_id).observed.get("rate_date") for c in verdict.citations
        }
        assert {"2026-08-14", "2026-08-17"} <= dates, dates

    def test_the_stale_rate_verdict_satisfies_the_evidence_criterion(self):
        """The S1 criterion, on a real verdict rather than a constructed one."""
        verdict, store = _investigate_live("US0378331005")
        hypothesis = verdict.to_hypothesis(
            store.as_mapping(), agent_ref="fx-rates-investigator@1.3.0"
        )
        cited = frozenset().union(*(e.observed.cited() for e in hypothesis.evidence))
        assert {"rate", "rate_date"} <= cited
        assert any(e.source_uri and e.retrieved_at for e in hypothesis.evidence)

    def test_it_stays_within_its_tool_budget(self):
        _, store = _investigate_live("US0378331005")
        assert len(store) <= agent_surface.DEFAULT_CALL_BUDGET

    def test_it_identifies_the_inverted_cross(self):
        verdict, _ = _investigate_live("GB00BN7SWP63")
        assert verdict.asserts_a_cause, verdict.unresolved
        assert "0.855" in verdict.root_cause or "1.1695" in verdict.root_cause
