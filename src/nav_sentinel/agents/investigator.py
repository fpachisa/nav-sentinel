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
the runner, and a cross-field rule ("evidence unless UNKNOWN") raises there -- unrecoverable, and it
would surface as a framework error rather than a bad answer. So the schema ADK sees accepts shapes
`Verdict` rejects, and the strict version is constructed afterwards.

There is **no repair retry**, and an earlier version of this docstring claimed one. A reply that
does not parse becomes a refusal with `reason="the investigator's answer could not be parsed"` --
distinct from an evidence refusal, because "the model returned prose" and "the filing was blocked"
are different findings and collapsing them would hide the first. A second model call to fix a
malformed answer costs a call for marginal gain when the fallback is already a clean, honest
refusal; if measurement later shows malformed replies are common, that is the point to add one.
"""

from __future__ import annotations

import logging
import re
from decimal import Decimal
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from nav_sentinel.agents.contract import (
    UNKNOWN,
    Citation,
    UnknownObservation,
    Verdict,
    refusal,
    resolve_citations,
)
from nav_sentinel.control_plane import agent_surface, gateway, identity, telemetry
from nav_sentinel.control_plane.extraction import ExtractionFailed, ExtractionRejected
from nav_sentinel.control_plane.gateway import ContentUnscreenable, ToolFailed
from nav_sentinel.control_plane.model_armor import ContentBlocked
from nav_sentinel.control_plane.observations import Observation, ObservationStore
from nav_sentinel.control_plane.policies import PolicyViolation

if TYPE_CHECKING:  # pragma: no cover
    from nav_sentinel.domain.models import ExceptionCase
    from nav_sentinel.registry.models import AgentManifest

logger = logging.getLogger(__name__)

APP_NAME = "nav-sentinel"

#: Failures of *evidence*. Each becomes a verdict that asserts nothing and escalates.
#:
#: `ValidationError` is deliberately absent. It is raised by our own model construction as readily
#: as by a bad model reply -- a long fact value overflowing `Citation.relevance` produced
#: "evidence refused: ValidationError", reporting a platform bug as a finding about the document.
#: A reply that does not parse raises `UnparseableAnswer` instead, which is handled separately.
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
)


class UnparseableAnswer(RuntimeError):
    """The model's reply was empty or not in the requested shape."""


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
    required = ", ".join(gateway.evidence_requirement_for(case.capability)) or "no particular facts"
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
  2. Every tool result comes back as {{"observation_id": ..., "result": ...}}. Keep every
     observation_id you receive.
  3. State the root cause in one sentence, quoting the specific values that show it -- the dates,
     the rates, the amounts, the currency. "The rate was wrong" is not a root cause. "The
     2026-08-14 USD rate of 1.1567 was applied to a 2026-08-17 valuation, where the published rate
     was 1.1593" is.
  4. In `observation_ids`, list **every** observation_id whose result you used, not just the last
     one. If your sentence quotes a rate, the lookup that returned that rate must be in the list.

Your answer is checked mechanically before it is accepted, and rejected if:
  - it asserts a cause but cites no observations;
  - the values your sentence quotes cannot be found in the observations you cited;
  - the observations you cited do not between them carry {required}.

Those checks compare your words against what your tool calls actually returned, so quote figures
exactly as the tools gave them and cite every call you drew on.

If the evidence does not support a cause, return root_cause exactly "{UNKNOWN}" with confidence 0.0
and say in `unresolved` what you could not establish. That is a useful answer, and it is the right
one when you are unsure. A confident wrong answer is not.
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
            except PolicyViolation as exc:
                # P-007 judges the agent's own *answer*, not its right to act, so its denial is a
                # verdict rather than an escape -- while every other policy denial still propagates.
                # `PolicyViolation` is not in EVIDENCE_FAILURES precisely so that P-001 through
                # P-006 keep surfacing as denials; softening those would turn "this agent was
                # denied a tool it must never call" into ordinary uncertainty.
                if exc.decision.policy_id != "P-007-EVIDENCE-CORROBORATION":
                    raise
                logger.info("%s uncorroborated on %s: %s", manifest.ref, case.case_id, exc)
                span.set_attribute("nav.verdict.refused", True)
                span.set_attribute("nav.verdict.refused_by", exc.decision.policy_id)
                return (
                    refusal(
                        case.case_id, capability,
                        reason=f"[{exc.decision.policy_id}] {exc.decision.reason}",
                    ),
                    store,
                )
            except UnknownObservation as exc:
                # A model paraphrasing or inventing an id is an ordinary failure mode, not a crash.
                logger.info("%s cited an unknown observation on %s: %s",
                            manifest.ref, case.case_id, exc)
                span.set_attribute("nav.verdict.refused", True)
                return refusal(case.case_id, capability, reason=str(exc)), store
            except UnparseableAnswer as exc:
                logger.warning("%s returned an unusable answer on %s: %s",
                               manifest.ref, case.case_id, exc)
                span.set_attribute("nav.verdict.unparseable", True)
                return (
                    refusal(
                        case.case_id, capability,
                        reason=f"the investigator's answer could not be parsed: {exc}",
                    ),
                    store,
                )
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
    from google.adk.agents.run_config import RunConfig
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
        # The tool budget bounds tool calls, not model turns: once it is spent the wrapper returns
        # `{"error": ...}` indefinitely, so a stubborn model could burn ADK's default 500 turns on
        # one case. This is the cost bound.
        run_config=RunConfig(max_llm_calls=25),
    ):
        if event.content and event.content.parts:
            for part in event.content.parts:
                if part.text:
                    reply = part.text

    # Prefer what ADK already validated. `output_key` puts the schema-checked draft in session
    # state, and ADK's own path strips code fences and ignores `thought` parts on the way -- neither
    # of which the text scrape below does. Measured: a fenced reply, or a thinking summary emitted
    # after the answer, left the correct draft sitting in state while the text parse raised and the
    # whole investigation was reported as an evidence refusal. A formatting artefact should not read
    # as "the filing was blocked".
    validated = (await runner.session_service.get_session(
        app_name=APP_NAME, user_id="fleet", session_id=session.id
    )).state.get("verdict")
    if validated:
        return VerdictDraft.model_validate(validated)
    if not reply.strip():
        raise UnparseableAnswer("the investigator returned nothing")
    try:
        return VerdictDraft.model_validate_json(reply)
    except ValidationError as exc:
        # Distinct from an evidence failure. Reported as what it is: the model did not answer in
        # the shape it was asked for. Lumping it in with ContentBlocked would make "the filing was
        # blocked" and "the model wrote prose" the same finding.
        raise UnparseableAnswer(f"reply was not a valid verdict: {str(exc)[:200]}") from exc


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
    # Normalised. `asserted = root_cause.strip() != UNKNOWN` was case- and punctuation-sensitive,
    # so "unknown", "Unknown" and "UNKNOWN." were all confident diagnoses at whatever confidence
    # the model returned -- rendered in the CLI's green panel and written into the domain as a
    # hypothesis. One character defeated `Verdict`'s own "cannot be held confidently" validator.
    stated = draft.root_cause.strip()
    asserted = bool(stated) and stated.strip(" .;:").upper() != UNKNOWN

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
    cited = resolve_citations(verdict, store.as_mapping())

    # P-007: the facts those observations carry must satisfy what the process demands.
    gateway.authorize_verdict(capability, store.facts_from([o.observation_id for o in cited]))

    # And the verdict must actually *use* the evidence it cites.
    ungrounded = unquoted_evidence(verdict, cited, gateway.evidence_requirement_for(capability))
    if ungrounded:
        return refusal(
            case.case_id,
            capability,
            reason=(
                f"the verdict does not state the evidence it cites: {', '.join(ungrounded)} appear "
                f"nowhere in its stated cause. A cause must quote the values it rests on."
            ),
            evidence=cited[0],
        )
    return verdict


def unquoted_evidence(
    verdict: Verdict, cited: list[Observation], required: tuple[str, ...]
) -> list[str]:
    """Required facts whose recorded values appear nowhere in the verdict's own words.

    The hole this closes, measured: a real GBP lookup returning 0.855 authorised
    *"Accounting applied the stale 2026-08-11 EUR/USD rate of 9.9999 instead of the published rate
    7.7777 to ISIN XX9999999999"* -- every number, the pair, the dates and the ISIN invented. The
    citation was genuine, the observation was recorded for that case by that agent, P-007 allowed
    it because the fact *names* `rate` and `rate_date` were present, and the S1 criterion was met.
    Only the citation was real; nothing tied it to the claim.

    So the rule is the other way round from "no ungrounded numbers". A verdict must **quote what it
    cites**: for every fact the process requires, some cited observation's recorded value for it has
    to appear in the root cause. Numbers are compared as decimals, so `1.15670000` in the books
    matches `1.1567` in the sentence; dates and short codes like `USD` are matched as text.

    Requiring instead that *every* number in the text be traceable would reject correct arithmetic --
    a withholding of 38,062.50 is derived from a gross of 253,750.00 and a rate of 15%, and none of
    the derived figures is a recorded fact. Demanding the inputs be quoted is the check that
    distinguishes reasoning from invention.

    Long string facts -- a filing name -- are skipped: a verdict is not expected to recite a
    filename, and `filing` is a citable fact so a reviewer can find the document, not so the model
    can quote it.
    """
    text = verdict.root_cause
    missing: list[str] = []
    for fact in required:
        values = [o.observed[fact] for o in cited if o.observed.get(fact)]
        if not values:
            continue  # P-007 already decided whether an absent fact is acceptable
        if not any(_appears_in(value, text) for value in values):
            missing.append(f"{fact}={values[0]}")
    return missing


#: What a verdict is expected to state: figures, the dates they belong to, and the currency they
#: are denominated in. `USD`, `GBP/EUR`.
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_CURRENCY_CODE = re.compile(r"[A-Z]{3}(?:/[A-Z]{3})?")


def _appears_in(value: str, text: str) -> bool:
    """Whether a recorded value is stated in the verdict's prose.

    Decimals are compared numerically because the books carry more precision than a sentence does:
    `1.15670000` and `1.1567` are the same rate. A percentage may legitimately be written either
    scaled or unscaled -- `0.15` recorded, "15%" written -- so both forms count.
    """
    try:
        recorded = Decimal(value)
    except (ArithmeticError, ValueError):
        # Dates and currency codes are matched as text; anything else is an identifier rather than
        # a figure, and a verdict is not expected to recite it.
        #
        # Classified by shape, not by length. A length cut-off was tried and `ca_notice_abev_clean
        # .txt` sat one character over it, so a filename was demanded of the prose -- the rule has
        # to say *what kind of thing* must be quoted, not how long it is.
        if _ISO_DATE.fullmatch(value) or _CURRENCY_CODE.fullmatch(value):
            return value in text
        return True

    candidates = {recorded, recorded * 100, recorded / 100}
    for literal in re.findall(r"-?\d[\d,]*(?:\.\d+)?", text):
        try:
            written = Decimal(literal.replace(",", ""))
        except ArithmeticError:
            continue
        if any(written == candidate for candidate in candidates):
            return True
    return False


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
    # Truncated to `Citation.relevance`'s own limit. Unbounded, a long fact value raised a
    # ValidationError from our own construction -- which `EVIDENCE_FAILURES` then reported as
    # "evidence refused", turning a platform bug into a finding about the filing.
    note = f"{observation.tool}: {facts}" if facts else f"{observation.tool} was called"
    return note[:400]
