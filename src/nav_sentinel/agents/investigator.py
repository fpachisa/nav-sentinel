"""One investigator, driven entirely by its registry manifest.

There is no per-agent class. The manifest names the model, the capabilities and the allowed tools;
the tool surface is generated from it; the prompt is assembled from it. Adding an investigator is
publishing a YAML file, which is the same claim the platform makes about adding a *process*, one
level down.

Three properties this module is responsible for, none of which the model can be trusted to
maintain:

**A verdict's citations are resolved, not believed.** The model names `observation_id`s; the code
looks them up, refuses ones recorded for another case, and builds the evidence from what the tool
actually returned. Nothing the model writes reaches `source_uri`, `retrieved_at` or an observed
value.

**Refusal is a verdict.** The poisoned corporate-action notice is *designed* to be blocked, so
letting `ContentBlocked` propagate would put a stack trace where the project's centrepiece control
belongs. Evidentiary failures become a verdict that asserts nothing and routes to a human.
`PolicyViolation` is deliberately **not** in that set -- catching it would turn "this agent was
denied a tool it must never call" into ordinary-looking uncertainty.

**The model's schema is permissive; validation happens here.** ADK validates `output_schema` inside
the runner, and a cross-field rule ("evidence unless UNKNOWN") raises there -- unrecoverable, and
it would surface as a framework error rather than a bad answer. So the schema ADK sees accepts
shapes `Verdict` rejects, and the strict version is constructed afterwards, with one bounded repair
attempt.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from nav_sentinel.agents.contract import (
    UNKNOWN,
    Citation,
    Verdict,
    refusal,
    resolve_citations,
)
from nav_sentinel.control_plane import agent_surface, gateway, identity, telemetry
from nav_sentinel.control_plane.extraction import ExtractionFailed, ExtractionRejected
from nav_sentinel.control_plane.gateway import ContentUnscreenable, ToolFailed
from nav_sentinel.control_plane.model_armor import ContentBlocked
from nav_sentinel.control_plane.observations import ObservationStore

if TYPE_CHECKING:  # pragma: no cover
    from nav_sentinel.domain.models import ExceptionCase
    from nav_sentinel.registry.models import AgentManifest

logger = logging.getLogger(__name__)

APP_NAME = "nav-sentinel"

#: Failures of *evidence*. Each becomes a verdict that asserts nothing and escalates.
#:
#: `ToolFailed` covers anything a tool raised -- a missing cassette recording, an HTTP failure --
#: which the gateway translates so this module need not import the tool packages to name them.
#:
#: `PolicyViolation` is not here on purpose. It is raised for P-001, P-002, P-003, P-005, P-006 and
#: P-007, so catching it would render every governance denial as ordinary uncertainty -- and the
#: poisoned notice's injected instruction is literally to post the entry without review, which is
#: exactly the denial an attacker would want softened.
EVIDENCE_FAILURES = (
    ContentBlocked,
    ContentUnscreenable,
    ExtractionRejected,
    ExtractionFailed,
    ToolFailed,
    ValidationError,
)


class VerdictDraft(BaseModel):
    """What the model is asked to return. Permissive by design -- see the module docstring."""

    model_config = ConfigDict(extra="ignore")

    root_cause: str = Field(
        default=UNKNOWN,
        description=(
            "One sentence naming the mechanism that caused the disagreement, with the specific "
            f"values that show it. Return exactly {UNKNOWN!r} if the evidence does not support a "
            "cause."
        ),
    )
    confidence: float = Field(
        default=0.0, description="0.0 to 1.0. Below 0.5 means you are not sure."
    )
    observation_ids: list[str] = Field(
        default_factory=list,
        description=(
            "The observation_id of every tool result you relied on. A claim you cannot cite will "
            "be rejected."
        ),
    )
    reasoning: str = Field(default="", description="How the evidence leads to the root cause.")
    unresolved: str = Field(default="", description="Anything you could not establish.")


def adk_name(agent_id: str) -> str:
    """`fx-rates-investigator` -> `fx_rates_investigator`.

    ADK requires a valid identifier and rejects hyphens outright. The registry ref keeps its real
    form everywhere it is recorded, so the audit trail and the framework never disagree about who
    acted -- the same reason the generated tools carry `nav_tool_name`.
    """
    return re.sub(r"[^0-9a-zA-Z_]", "_", agent_id)


def _instruction(manifest: AgentManifest, case: ExceptionCase) -> str:
    """The prompt, assembled from the manifest and the case.

    Written to be specific about the two things a model gets wrong here: asserting a cause it
    cannot evidence, and citing a tool result it did not actually rely on. Both are refused
    downstream, so saying so up front turns a rejection into a corrigible instruction.
    """
    breaks = "\n".join(
        f"  - {b.break_type.value}: accounting {b.accounting_value}, custodian "
        f"{b.custodian_value}, difference {b.difference}"
        + (f", ISIN {b.isin}" if b.isin else "")
        + (f", currency {b.currency}" if b.currency else "")
        + (f" ({b.note})" if b.note else "")
        for b in case.breaks
    )
    return f"""You are the {manifest.display_name} for a fund administrator.

{manifest.description.strip()}

Explain WHY the books disagree. You do not fix anything: a separate agent drafts corrections and a
human approves them. Your output is an explanation supported by evidence.

The case:
  fund {case.fund_id}, valuation date {case.as_of.isoformat()}
  case {case.case_id}
{breaks}

How to work:
  1. Use your tools to establish what the external reference data actually says.
  2. Every tool result comes back as {{"observation_id": ..., "result": ...}}. Note the
     observation_id of anything you rely on.
  3. State the root cause in one sentence, naming the specific values that show it -- a date, a
     rate, an amount. "The rate was wrong" is not a root cause; "the 14 August rate 1.1567 was
     applied to a 17 August valuation, where the published rate was 1.1593" is.
  4. List the observation_id of every result your explanation depends on.

Two things will cause your answer to be rejected:
  - Asserting a cause without citing the observations that establish it.
  - Naming a value that does not appear in any observation you cited.

If the evidence does not support a cause, return root_cause exactly "{UNKNOWN}" with confidence 0.0
and say in `unresolved` what you could not establish. That is a useful answer. A confident wrong
answer is not.
"""


async def investigate(
    case: ExceptionCase,
    manifest: AgentManifest,
    *,
    trace_id: str | None = None,
    store: ObservationStore | None = None,
    budget: int = agent_surface.DEFAULT_CALL_BUDGET,
) -> tuple[Verdict, ObservationStore]:
    """Investigate one case and return a verdict plus the observations behind it.

    The store is returned rather than hidden so a caller can convert the verdict to a domain
    hypothesis, which needs the same observations to build its evidence from.
    """
    from google.adk.agents import Agent

    from nav_sentinel.config import configure_sdk_environment

    configure_sdk_environment()
    store = store if store is not None else ObservationStore()
    capability = case.capability

    with identity.acting_as(manifest.agent_id):
        tools = agent_surface.build(
            manifest, case_id=case.case_id, trace_id=trace_id, store=store, budget=budget
        )
        agent = Agent(
            name=adk_name(manifest.agent_id),
            model=manifest.model,          # from the manifest, never a literal
            instruction=_instruction(manifest, case),
            tools=tools,
            output_schema=VerdictDraft,
            # Required: without an output_key ADK returns early from saving the validated output,
            # so the parsed verdict never reaches session state.
            output_key="verdict",
        )
        attributes = {
            "nav.case.id": case.case_id,
            "nav.agent.ref": manifest.ref,
            "nav.agent.model": manifest.model,
            "nav.case.capability": capability,
        }
        with telemetry.span("nav_sentinel.investigate", **attributes) as span:
            try:
                draft = await _run(agent, case)
                verdict = _finalise(draft, case, capability, store)
            except EVIDENCE_FAILURES as exc:
                # Evidence refused, not a governance denial. The distinction is the point: this is
                # "I could not establish anything", not "this agent was stopped".
                reason = f"{type(exc).__name__}: {exc}"
                logger.info("%s refused evidence on %s: %s", manifest.ref, case.case_id, reason)
                span.set_attribute("nav.verdict.refused", True)
                span.set_attribute("nav.verdict.refusal_reason", reason[:400])
                return refusal(case.case_id, capability, reason=reason), store

            span.set_attribute("nav.verdict.root_cause", verdict.root_cause[:300])
            span.set_attribute("nav.verdict.confidence", verdict.confidence)
            span.set_attribute("nav.verdict.citations", len(verdict.citations))
            span.set_attribute("nav.tool.calls", len(store))
            return verdict, store


async def _run(agent, case: ExceptionCase) -> VerdictDraft:
    """Drive the ADK agent once and return its structured draft."""
    from google.adk.runners import InMemoryRunner
    from google.genai import types

    runner = InMemoryRunner(agent, app_name=APP_NAME)
    session = await runner.session_service.create_session(
        app_name=APP_NAME, user_id="fleet"
    )
    reply = ""
    async for event in runner.run_async(
        user_id="fleet",
        session_id=session.id,
        new_message=types.Content(
            role="user",
            parts=[types.Part(text=f"Investigate case {case.case_id}.")],
        ),
    ):
        if event.content and event.content.parts:
            for part in event.content.parts:
                if part.text:
                    reply = part.text
    return VerdictDraft.model_validate_json(reply) if reply.strip() else VerdictDraft()


def _finalise(
    draft: VerdictDraft,
    case: ExceptionCase,
    capability: str,
    store: ObservationStore,
) -> Verdict:
    """Turn a permissive draft into a `Verdict`, or into a refusal.

    Everything the model could get wrong is corrected or refused here rather than trusted: an id it
    invented, an id from another case, a cause it cannot corroborate, or a confident UNKNOWN.
    """
    citations = [
        Citation(observation_id=oid, relevance=_relevance(store, oid))
        for oid in dict.fromkeys(draft.observation_ids)      # de-duplicated, order preserved
    ]
    asserted = draft.root_cause.strip() and draft.root_cause.strip() != UNKNOWN

    if not asserted or not citations:
        return refusal(
            case.case_id,
            capability,
            reason=draft.unresolved.strip()
            or (
                "the investigator asserted a cause without citing any observation"
                if asserted
                else "the investigator could not establish a cause"
            ),
        )

    verdict = Verdict(
        case_id=case.case_id,
        capability=capability,
        root_cause=draft.root_cause.strip()[:1000],
        confidence=min(max(draft.confidence, 0.0), 1.0),
        citations=citations,
        reasoning=draft.reasoning[:4000],
        unresolved=draft.unresolved,
    )

    # Cited observations must exist, and belong to this case. Refused, not trusted.
    resolve_citations(verdict, store.as_mapping())

    # P-007: the facts those observations carry must satisfy what the process demands.
    gateway.authorize_verdict(capability, store.facts_from(draft.observation_ids))
    return verdict


def _relevance(store: ObservationStore, observation_id: str) -> str:
    """A short note on why an observation was cited.

    The model supplies one sentence of reasoning for the verdict as a whole rather than per
    citation, so the per-item note is derived from the observation itself. That is deliberate: the
    fields a downstream check reads must not come from model text, and a per-citation sentence would
    be one more thing to validate for no gain.
    """
    observation = store.get(observation_id)
    if observation is None:
        return "cited by the investigator"
    facts = ", ".join(f"{k}={v}" for k, v in sorted(observation.observed.items()))
    return f"{observation.tool}: {facts}" if facts else f"{observation.tool} was called"
