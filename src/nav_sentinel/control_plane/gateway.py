"""The Agent Gateway: the single enforcement point.

Every tool call, every drafting attempt and every posting attempt passes through here. The
gateway reads the calling agent's registry manifest, evaluates policy, records the decision
on the trace, and either proceeds or refuses.

Enforcement lives outside the agents on purpose. An agent that checks its own permissions is
one prompt away from deciding it has them.
"""

from __future__ import annotations

from contextvars import ContextVar
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from nav_sentinel.control_plane import identity, packs, policies, telemetry
from nav_sentinel.control_plane.extraction import ExtractionRejected
from nav_sentinel.control_plane.governance import CaseFacts

# The exception only. `model_armor` defers the Google SDK import into its own
# functions, so naming it here costs nothing at import time.
from nav_sentinel.control_plane.model_armor import ContentBlocked
from nav_sentinel.control_plane.policies import Effect, PolicyDecision, PolicyViolation


class ContentUnscreenable(RuntimeError):
    """An untrusted tool returned a value the screener cannot inspect. Fail closed."""


class ToolFailed(RuntimeError):
    """A tool raised while executing. Not a policy denial, and not a screening block.

    The gateway translates whatever a tool raises into this, so an agent can distinguish "my
    evidence could not be obtained" from "I was refused" without importing the tool modules --
    which would give the agents layer the ungated callables the seam exists to keep away from it.

    The original is kept on `cause` and chained, so the audit trail loses nothing.
    """

    def __init__(self, tool_name: str, cause: BaseException) -> None:
        super().__init__(f"{tool_name} failed: {type(cause).__name__}: {cause}")
        self.tool_name = tool_name
        self.cause = cause


# Every decision, in order, for the current unit of work. The exception console renders this as
# the governance log and the demo reads from it directly.
#
# A ContextVar, not a module-level list, because this was a process-global list on a service
# deployed with Cloud Run's default concurrency of 80. Both HTTP handlers call
# `clear_decision_log()` and then report `len(decision_log())`, so concurrent requests destroyed
# each other's audit records: measured, one cycle serially recorded 28 decisions while eight
# concurrent cycles reported 80, 28, 54, 132, 184, 106, 158 and 210. For a project whose
# deliverable *is* the audit trail, that loses audit records under ordinary operation.
#
# asyncio gives each request task its own context, so per-request isolation comes for free --
# the same reason `identity` is already a ContextVar.
#
# One property to know before S3 fans work out across tasks: a child task inherits a *copy of the
# context*, which holds the same list object, so a child's decisions append to the parent's log.
# That is what we want for per-case fan-out -- the parent's audit trail should contain what its
# children decided -- but it means a child must NOT call `clear_decision_log()`, which would
# rebind only its own context and hide its records from the parent.
_decision_log: ContextVar[list[PolicyDecision]] = ContextVar("nav_decision_log")


def _log() -> list[PolicyDecision]:
    """The current context's log, created on first use."""
    try:
        return _decision_log.get()
    except LookupError:
        fresh: list[PolicyDecision] = []
        _decision_log.set(fresh)
        return fresh


def decision_log() -> list[PolicyDecision]:
    return list(_log())


def clear_decision_log() -> None:
    """Start a fresh log for this context. Cannot affect a concurrent request."""
    _decision_log.set([])
    _marks.set({})


def decisions_since(marker: str) -> list[PolicyDecision]:
    """Every decision recorded since `mark_decisions` was last called with this marker.

    A caller persisting one case's decisions needs *that case's* decisions, and the log is
    per-context rather than per-case -- a cycle works several cases in one context. Clearing the log
    between cases would work but would also throw away the running trail the console reads, so the
    boundary is marked instead.
    """
    return list(_log()[_markers().get(marker, 0):])


def mark_decisions(marker: str) -> None:
    """Record where the log stands, so `decisions_since` can report what followed."""
    _markers()[marker] = len(_log())


_marks: ContextVar[dict[str, int]] = ContextVar("nav_decision_marks")


def _markers() -> dict[str, int]:
    try:
        return _marks.get()
    except LookupError:
        fresh: dict[str, int] = {}
        _marks.set(fresh)
        return fresh


def restore_decision_log(decisions: list[PolicyDecision]) -> None:
    """Put back a snapshot, discarding anything recorded since it was taken.

    For self-tests and probes, which necessarily exercise real policy paths and would otherwise
    leave fabricated ALLOW/DENY records -- attributed to a real published agent -- in the log the
    exception console renders as the governance trail.

    Mutates the list in place rather than rebinding the ContextVar. Rebinding only replaced this
    context's view: when the list object is shared with an outer context -- which is the whole
    point of the sharing property documented above, and what S3's fan-out will rely on -- the
    fabricated records survived in the parent while the child's own view looked clean.
    """
    current = _log()
    current[:] = list(decisions)


def _record(decision: PolicyDecision) -> PolicyDecision:
    _log().append(decision)
    telemetry.record_policy_decision(decision.as_span_attributes())
    return decision


def _enforce(decision: PolicyDecision) -> PolicyDecision:
    _record(decision)
    if decision.effect is Effect.DENY:
        raise PolicyViolation(decision)
    return decision


def call_tool(tool_name: str, *args: Any, **kwargs: Any) -> Any:
    """Invoke a tool by name on behalf of the currently bound agent identity.

    The callable is resolved from the tool catalogue, never accepted from the caller. The
    previous signature took the function as an argument and validated only the name, so any
    function could run under a declared tool's label while the audit record named the label.

    Raises IdentityError when no identity is bound, UnknownTool for a name absent from the
    catalogue, and PolicyViolation when the manifest does not permit the tool or its data
    scope. All four are failures, not warnings.
    """
    manifest = identity.current()
    try:
        spec = packs.resolve(tool_name)
    except packs.UnknownTool:
        # Recorded, then re-raised. "Agent X attempted tool Y, which does not exist" is a
        # governance event -- an agent enumerating tool names must not be invisible in the
        # log. Still UnknownTool rather than PolicyViolation, so a manifest typo remains
        # distinguishable from a refusal to use a real tool.
        _record(
            PolicyDecision(
                effect=Effect.DENY,
                policy_id="P-001-TOOL-ALLOWLIST",
                reason=f"{tool_name!r} does not exist in any registered process pack",
                agent_ref=manifest.ref,
                resource=tool_name,
            )
        )
        raise

    # Refuse callable arguments outright. Resolution from the catalogue already makes the old
    # swap impossible, but without this the attempt is consumed as a tool argument and fails
    # as an obscure TypeError inside the tool -- after both policies have logged ALLOW. A
    # rejected call should be refused for the real reason and recorded as such.
    offending = [
        f"positional #{i}" for i, a in enumerate(args) if callable(a)
    ] + [f"keyword {k!r}" for k, v in kwargs.items() if callable(v)]
    if offending:
        raise TypeError(
            f"call_tool({tool_name!r}) received a callable as {', '.join(offending)}. "
            f"Tools are resolved from the catalogue by name; a function cannot be supplied "
            f"by the caller."
        )

    # Everything downstream names spec.name -- what actually runs -- rather than the key the
    # caller passed, so the audit record cannot describe a different tool than executed.
    resolved = spec.name
    _enforce(policies.tool_allowed(manifest, resolved))
    _enforce(policies.tool_within_data_scope(manifest, resolved, spec.reads))

    with telemetry.span(
        "gateway.tool_call",
        **{
            "nav.agent.ref": manifest.ref,
            "nav.agent.service_account": identity.service_account_email(manifest),
            "nav.tool.name": resolved,
            "nav.tool.reads": list(spec.reads),
            "nav.tool.untrusted_output": spec.untrusted_output,
        },
    ):
        try:
            result = spec.fn(*args, **kwargs)
        except (PolicyViolation, ContentUnscreenable):
            # A denial raised from inside a tool is still a denial. Never reclassify it as a tool
            # failure: an agent's refusal path treats the two completely differently.
            raise
        except (ContentBlocked, ExtractionRejected):
            # A control refusing is the control working, not a malfunction. Wrapping these made
            # "Model Armor caught an injection in this filing" and "the extractor rejected a
            # document whose withholding contradicts the treaty schedule" both read as "the tool
            # crashed" -- and those two findings are the most important things the
            # corporate-action path reports.
            raise
        except Exception as exc:
            # Translated, so an agent never needs to import a tool module to know what can go
            # wrong. Importing `nav_sentinel.tools.ecb_fx` for its `CassetteMiss` would hand the
            # agents layer the ungated callables in that module -- which is exactly what the seam
            # forbids, and what caught this.
            raise ToolFailed(resolved, exc) from exc

    # Screening is bound to the tool that fetched the bytes, not to an agent's self-declared
    # flag. An agent cannot forget to screen, because it never had the option.
    if spec.untrusted_output:
        return _screen_untrusted_result(
            result, source_uri=f"tool:{resolved}", fields=spec.untrusted_fields
        )
    return result


#: Distinct strings screened per tool call. `edgar.recent_filings` returns up to 1000 filings of
#: 7 fields; screening every one produced 15,000 sanitize calls for a single call, with only ten
#: distinct payloads -- and 15,000 spans overflowed the batch processor, so audit spans were
#: silently dropped. The audit trail is the deliverable, so that is the serious half.
MAX_SCREENED_STRINGS = 64


def _screen_untrusted_result(
    value: Any, *, source_uri: str, fields: tuple[str, ...] = (), _depth: int = 0
) -> Any:
    """Screen every string reachable in an untrusted tool's return value.

    An earlier version screened only a bare `str`, so a tool returning a dict or a list bypassed
    screening entirely while still logging ALLOW. Attacker-controllable text routinely arrives
    inside a structure -- a filing's `description` field, a list of search hits -- so the shape of
    the container must not decide whether a control applies.

    Un-screenable types are refused rather than passed through: a control that silently ignores
    what it does not understand is not a control.
    """
    seen: dict[str, str] = {}
    budget = [MAX_SCREENED_STRINGS]
    return _screen_value(
        value, source_uri=source_uri, seen=seen, budget=budget, depth=_depth,
        fields=frozenset(fields), screen_this=not fields,
    )


def _screen_value(  # noqa: PLR0911 -- one return per admissible shape; collapsing them would
    #                  hide which shapes are handled and which are refused.
    value: Any, *, source_uri: str, seen: dict[str, str], budget: list[int],
    depth: int, fields: frozenset[str], screen_this: bool,
) -> Any:
    if depth > 6:
        raise ContentUnscreenable("untrusted value nests more deeply than 6 levels")

    if isinstance(value, str):
        if not screen_this:
            # A field the tool did not declare as filer-authored. Not screened, and not silently
            # trusted either: it is returned unchanged precisely because it cannot carry prose.
            return value
        # Memoised by content. The same issuer name repeats across every filing in a listing, and
        # screening it a thousand times costs a thousand calls to learn one fact.
        if value in seen:
            return seen[value]
        if not value.strip():
            return value
        if budget[0] <= 0:
            raise ContentUnscreenable(
                f"more than {MAX_SCREENED_STRINGS} distinct strings to screen in one tool "
                f"result. Refusing rather than spending an unbounded number of screening calls "
                f"and overflowing the span queue that carries the audit trail."
            )
        budget[0] -= 1
        screened = admit_untrusted_content(value, source_uri=source_uri)
        seen[value] = screened
        return screened

    if isinstance(value, (int, float, bool, type(None), Decimal, date, datetime)):
        return value
    if isinstance(value, dict):
        # Keys are our own field names, not filer text, so they are not screened.
        return {
            k: _screen_value(
                v, source_uri=source_uri, seen=seen, budget=budget, depth=depth + 1,
                fields=fields, screen_this=(not fields) or (k in fields),
            )
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        screened = [
            _screen_value(
                v, source_uri=source_uri, seen=seen, budget=budget, depth=depth + 1,
                fields=fields, screen_this=screen_this,
            )
            for v in value
        ]
        return set(screened) if isinstance(value, set) else type(value)(screened)
    raise ContentUnscreenable(
        f"a tool declared untrusted_output returned {type(value).__name__}, which cannot be "
        f"screened. Return a string or a structure of primitives, or screen it explicitly."
    )


def authorize_drafting() -> PolicyDecision:
    """P-002. Subject resolved from the bound identity, never passed in.

    This took the manifest as an argument, so the decision was driven by whichever document the
    caller handed over -- a copy with `may_propose_remediation` set true was enough to escalate.
    """
    return _enforce(policies.may_propose_remediation(identity.current()))


def authorize_posting(
    facts: CaseFacts, human_approval_ref: str | None = None
) -> PolicyDecision:
    """P-003. The last gate before the ledger.

    Subject resolved from the bound identity; the approval reference is resolved against the
    approvals store rather than trusted as a string. With the fleet as published this denies
    unconditionally, which is the intended behaviour rather than a limitation.
    """
    return _enforce(
        policies.may_post_entry(identity.current(), facts, human_approval_ref)
    )


def route_for_approval(facts: CaseFacts) -> PolicyDecision:
    return _record(policies.approval_route(facts))


def prompt_dirs() -> tuple[Path, ...]:
    """Every registered process's prompt directory.

    Exposed here for the same reason as `capabilities()`: the agents layer must not import `packs`,
    because reading the catalogue means holding the ungated tool callables -- and the seam test
    caught exactly that when the prompt loader reached for it directly.
    """
    return packs.prompt_dirs()


def capabilities() -> tuple[str, ...]:
    """Every capability any registered process declares.

    Exposed here for the same reason as `evidence_requirement_for`: the agents layer must not
    import `packs`, because reading the catalogue means holding the ungated tool callables.
    """
    return packs.capabilities()


def evidence_requirement_for(capability: str) -> tuple[str, ...]:
    """What the owning process demands a verdict cite for this capability.

    Exposed here so the agents layer need not import `packs`: reading the catalogue means holding
    `ToolSpec.fn`, the live ungated callable, which is exactly what the seam keeps away from
    `agents/` -- and the seam test caught this being imported directly.
    """
    return packs.evidence_requirement_for(capability)


def authorize_verdict(capability: str, cited_facts: frozenset[str]) -> PolicyDecision:
    """P-007. The acting agent comes from the bound identity; the requirement from the process.

    Takes the *facts* the verdict's cited observations actually carry, as plain strings, so the
    control plane stays ignorant of what a verdict is. Enforced, not merely recorded: an
    uncorroborated assertion is refused, and the investigator turns that refusal into a verdict
    that asserts nothing.
    """
    manifest = identity.current()
    required = packs.evidence_requirement_for(capability)
    return _enforce(policies.verdict_is_corroborated(manifest, capability, cited_facts, required))


def admit_untrusted_content(text: str, *, source_uri: str | None = None) -> str:
    """Screen external content before it reaches a model context.

    The acting agent is resolved from the bound identity, never accepted as an argument.
    Taking it as a parameter meant the screening decision was driven by whichever manifest
    the caller passed -- so an agent could opt out of screening by supplying a copy of its own
    manifest with `untrusted_inputs` set to false.

    Screening is applied whenever the *content* is untrusted, which the tool catalogue
    determines. P-005 additionally refuses any agent that declares untrusted inputs while
    disclaiming the screening requirement.
    """
    from nav_sentinel.control_plane import model_armor

    manifest = identity.current()
    _enforce(policies.untrusted_ingest_requires_armor(manifest))

    with telemetry.span(
        "gateway.model_armor_screen",
        **{
            "nav.agent.ref": manifest.ref,
            "nav.armor.template": model_armor.template_path(),
            "nav.armor.source_uri": source_uri or "",
            "nav.armor.content_bytes": len(text.encode("utf-8")),
        },
    ) as sp:
        try:
            verdict = model_armor.screen(text, source_uri=source_uri)
        except model_armor.ContentBlocked as blocked:
            # The block is the fleet's most important governance event, so it is recorded as
            # a policy decision in the governance log -- not left as an OTel exception that
            # only appears in a trace viewer.
            _record(
                PolicyDecision(
                    effect=Effect.DENY,
                    policy_id="P-005-UNTRUSTED-INGEST",
                    reason=(
                        f"Model Armor blocked content from {source_uri or 'external source'}: "
                        f"{blocked.verdict.summary}"
                    ),
                    agent_ref=manifest.ref,
                    resource=source_uri or "external_content",
                )
            )
            sp.set_attribute("nav.armor.verdict", blocked.verdict.verdict)
            sp.set_attribute("nav.armor.blocked", True)
            sp.set_attribute("nav.armor.matched_filters", list(blocked.verdict.matched_filters))
            raise

        sp.set_attribute("nav.armor.verdict", verdict.verdict)
        sp.set_attribute("nav.armor.blocked", verdict.blocked)
        telemetry.record_evidence(
            sp, source=source_uri or "external", summary=verdict.summary,
            trusted=False, armor_verdict=verdict.verdict,
        )
        return text
