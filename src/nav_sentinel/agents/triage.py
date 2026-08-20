"""Triage: decide which capability a break belongs to, or decline to.

The cheap model, no tools. Triage classifies from the case facts; it does not investigate, and
giving it tools would let it start the work its own routing decision is supposed to hand to a
specialist.

Two properties are load-bearing.

**It cannot invent a category.** The output schema is an enum built from `gateway.capabilities()`,
so a capability no process declares has nowhere to land -- out-of-vocabulary answers become
`nav.unclassified` structurally rather than because the prompt asked nicely. `ExceptionCase.category`
is a closed enum, so anything else would have had nowhere to go anyway; the difference is whether
that surfaces as a refusal or as a crash.

**A confident wrong route is worse than an admitted miss.** A misrouted break reaches a specialist
that investigates the wrong thing, spends a tool budget, and returns a plausible answer about the
wrong mechanism. So below a confidence floor the answer is discarded and replaced with
`nav.unclassified`, which routes to a human.

Routing itself stays with the registry. Triage names a capability;
`discover.discover_for_capability` decides who -- if anyone -- is authorised to handle it. That
separation is what makes "correctly triaged, then refused for want of an authorised investigator" a
demonstrable state rather than a special case: `nav.pricing` is a declared capability of the NAV
process with no published agent, and the registry says so.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

from nav_sentinel.agents.investigator import UnparseableAnswer, adk_name
from nav_sentinel.control_plane import gateway, identity, telemetry
from nav_sentinel.domain import signals

if TYPE_CHECKING:  # pragma: no cover
    from nav_sentinel.domain.models import ExceptionCase
    from nav_sentinel.registry.models import AgentManifest

logger = logging.getLogger(__name__)

APP_NAME = "nav-sentinel"

#: Below this, the classification is discarded. A break routed confidently to the wrong specialist
#: is investigated by an agent with the wrong tools, which produces a plausible answer about the
#: wrong mechanism -- strictly worse than an admitted miss, which routes to a human.
CONFIDENCE_FLOOR = 0.5

UNCLASSIFIED = "nav.unclassified"


class Classification(BaseModel):
    """What triage decided, after the floor and the vocabulary check have been applied."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str
    capability: str
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str = ""
    #: Set when the model's own answer was overridden -- below the floor, or outside the vocabulary.
    #: Recorded rather than hidden, because "triage was unsure" and "triage was wrong" are different
    #: findings and the eval needs to tell them apart.
    overridden_from: str | None = None

    @property
    def classified(self) -> bool:
        return self.capability != UNCLASSIFIED


def draft_model() -> type[BaseModel]:
    """Build the output schema from the registered capabilities, at call time.

    Constructed per call rather than at import so a process registering later, or a pack being
    swapped in a test, changes the vocabulary. A schema frozen at import would quietly describe a
    fleet that no longer exists.
    """
    vocabulary = tuple(gateway.capabilities())
    if not vocabulary:
        raise RuntimeError(
            "no process declares any capability, so triage has nothing to classify into. "
            "Call nav_sentinel.composition.configure() first."
        )

    class TriageDraft(BaseModel):
        model_config = ConfigDict(extra="ignore")

        capability: Literal[vocabulary] = Field(  # type: ignore[valid-type]
            description=(
                "Which capability this break belongs to. Use "
                f"{UNCLASSIFIED!r} if none of the others fits."
            ),
        )
        confidence: float = Field(
            default=0.0,
            description=(
                "0.0 to 1.0. Below 0.5 the break is escalated to a human instead of being routed, "
                "so answer honestly rather than confidently."
            ),
        )
        reasoning: str = Field(default="", description="One sentence on what decided it.")

    return TriageDraft


def _instruction(manifest: AgentManifest, case: ExceptionCase) -> str:
    """The prompt. Describes the shapes rather than naming securities, so it does not encode the
    fixtures -- a classifier that recognises ISINs has learned the test, not the job.

    The `signals` block is the substance. Given only the two disagreeing totals the model scored 2
    of 6 with two confident wrong answers, and could not have done better: a market value that
    differs while quantity agrees is an FX error or a pricing error, and the totals cannot tell them
    apart. The books can, deterministically, so those facts are computed rather than guessed.
    """
    breaks = "\n".join(
        f"  - {b.break_type.value}: accounting {b.accounting_value}, custodian "
        f"{b.custodian_value}, difference {b.difference}"
        + (f", ISIN {b.isin}" if b.isin else "")
        + (f", currency {b.currency}" if b.currency else "")
        + (f" ({b.note})" if b.note else "")
        for b in case.breaks
    )
    evidence = "\n".join(f"  - {line}" for line in signals.for_case(case))
    return f"""You are {manifest.display_name} for a fund administrator.

Decide which kind of problem this reconciliation break is, so it can be routed to the right
specialist. You are not solving it.

  fund {case.fund_id}, valuation date {case.as_of.isoformat()}
  case {case.case_id}
{breaks}

What the books say about it:
{evidence}

What the categories look like:
  - nav.fx_rate: a market value differs while quantity agrees, and the difference is consistent
    with a currency conversion -- a rate from the wrong date, or a cross applied upside down.
  - nav.corporate_action: a dividend, split, merger or spin-off. A cash difference matching a
    withholding rate, or a quantity differing by a whole ratio while market value agrees exactly.
  - nav.settlement: the two books recognise the same trade on different dates, or one has a
    position the other does not. Trade date versus settlement date, or a failed delivery.
  - nav.pricing: the price itself differs -- a stale, wrong or manually overridden security price
    in the same currency. Not a conversion problem.
  - nav.cash_fees: management fees, performance fees or expense accruals.
  - {UNCLASSIFIED}: none of the above fits, or the evidence is genuinely ambiguous.

Answer {UNCLASSIFIED} when you are unsure. A break you send to the wrong specialist is investigated
with the wrong tools and comes back with a confident answer about the wrong mechanism, which is
worse than saying you do not know. Below 0.5 confidence your answer is discarded and the break goes
to a human anyway, so there is nothing to gain by overstating it.
"""


async def classify(
    case: ExceptionCase,
    manifest: AgentManifest,
    *,
    trace_id: str | None = None,
) -> Classification:
    """Classify one case. Never raises for a model mistake."""
    from google.adk.agents import Agent

    from nav_sentinel.config import configure_sdk_environment

    configure_sdk_environment()
    schema = draft_model()

    with identity.acting_as(manifest.agent_id):
        agent = Agent(
            name=adk_name(manifest.agent_id),
            model=manifest.model,
            instruction=_instruction(manifest, case),
            # No tools, deliberately: classifying is not investigating, and a triage agent holding
            # investigative tools would begin the work its own routing decision delegates.
            tools=[],
            output_schema=schema,
            output_key="classification",
        )
        with telemetry.span(
            "nav_sentinel.triage",
            **{
                "nav.case.id": case.case_id,
                "nav.agent.ref": manifest.ref,
                "nav.agent.model": manifest.model,
                # Recorded so a routing decision and the investigation it led to share one trace.
                "nav.case.trace_id": trace_id or "",
            },
        ) as span:
            try:
                draft = await _run(agent, case, schema)
            except (UnparseableAnswer, Exception) as exc:  # noqa: BLE001
                # A classifier that raises stops the whole cycle over one unusable answer. An
                # unclassified case is a supported outcome; a crashed one is not.
                logger.warning("triage failed on %s: %s", case.case_id, exc)
                span.set_attribute("nav.triage.failed", True)
                return Classification(
                    case_id=case.case_id,
                    capability=UNCLASSIFIED,
                    confidence=0.0,
                    reasoning=f"triage could not classify this break: {exc}",
                )

            result = _apply_floor(case.case_id, draft)
            span.set_attribute("nav.triage.capability", result.capability)
            span.set_attribute("nav.triage.confidence", result.confidence)
            if result.overridden_from:
                span.set_attribute("nav.triage.overridden_from", result.overridden_from)
            return result


def _apply_floor(case_id: str, draft: BaseModel) -> Classification:
    """Discard a classification the model is not confident enough to stand behind.

    The override is recorded in `overridden_from` rather than silently applied, because the eval has
    to distinguish "triage was unsure" from "triage was wrong" -- collapsing them would let a
    classifier that hedges everything score the same as one that is right.
    """
    capability = draft.capability
    confidence = min(max(draft.confidence, 0.0), 1.0)
    if capability != UNCLASSIFIED and confidence < CONFIDENCE_FLOOR:
        return Classification(
            case_id=case_id,
            capability=UNCLASSIFIED,
            confidence=confidence,
            reasoning=draft.reasoning,
            overridden_from=capability,
        )
    return Classification(
        case_id=case_id,
        capability=capability,
        confidence=confidence,
        reasoning=draft.reasoning,
    )


async def _run(agent, case: ExceptionCase, schema: type[BaseModel]) -> BaseModel:
    from google.adk.agents.run_config import RunConfig
    from google.adk.runners import InMemoryRunner
    from google.genai import types

    runner = InMemoryRunner(agent, app_name=APP_NAME)
    session = await runner.session_service.create_session(app_name=APP_NAME, user_id="fleet")
    reply = ""
    async for event in runner.run_async(
        user_id="fleet",
        session_id=session.id,
        new_message=types.Content(
            role="user", parts=[types.Part(text=f"Classify case {case.case_id}.")]
        ),
        # Classification is one turn. Anything more means the model is arguing with itself.
        run_config=RunConfig(max_llm_calls=3),
    ):
        if event.content and event.content.parts:
            for part in event.content.parts:
                if part.text:
                    reply = part.text

    # ADK's own validated output first: its path strips code fences and skips thought parts, which
    # a text scrape does not.
    validated = (
        await runner.session_service.get_session(
            app_name=APP_NAME, user_id="fleet", session_id=session.id
        )
    ).state.get("classification")
    if validated:
        return schema.model_validate(validated)
    if not reply.strip():
        raise UnparseableAnswer("triage returned nothing")
    return schema.model_validate_json(reply)
