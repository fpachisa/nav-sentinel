"""The Agent Gateway: the single enforcement point.

Every tool call, every drafting attempt and every posting attempt passes through here. The
gateway reads the calling agent's registry manifest, evaluates policy, records the decision
on the trace, and either proceeds or refuses.

Enforcement lives outside the agents on purpose. An agent that checks its own permissions is
one prompt away from deciding it has them.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from nav_sentinel.control_plane import identity, packs, policies, telemetry
from nav_sentinel.control_plane.governance import CaseFacts
from nav_sentinel.control_plane.policies import Effect, PolicyDecision, PolicyViolation


# Every decision, in order, for the life of the process. The exception console renders this
# as the governance log and the demo reads from it directly.
class ContentUnscreenable(RuntimeError):
    """An untrusted tool returned a value the screener cannot inspect. Fail closed."""


_decision_log: list[PolicyDecision] = []


def decision_log() -> list[PolicyDecision]:
    return list(_decision_log)


def clear_decision_log() -> None:
    _decision_log.clear()


def _record(decision: PolicyDecision) -> PolicyDecision:
    _decision_log.append(decision)
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
        result = spec.fn(*args, **kwargs)

    # Screening is bound to the tool that fetched the bytes, not to an agent's self-declared
    # flag. An agent cannot forget to screen, because it never had the option.
    if spec.untrusted_output:
        return _screen_untrusted_result(result, source_uri=f"tool:{resolved}")
    return result


def _screen_untrusted_result(value: Any, *, source_uri: str, _depth: int = 0) -> Any:
    """Screen every string reachable in an untrusted tool's return value.

    An earlier version screened only a bare `str`, so a tool returning a dict or a list of
    strings bypassed screening entirely while still logging ALLOW. Attacker-controllable text
    routinely arrives inside a structure -- a filing's `description` field, a list of search
    hits -- so the shape of the container must not decide whether a control applies.

    Un-screenable types are refused rather than passed through: a control that silently
    ignores what it does not understand is not a control.
    """
    if _depth > 6:
        raise ContentUnscreenable("untrusted value nests more deeply than 6 levels")

    if isinstance(value, str):
        return admit_untrusted_content(value, source_uri=source_uri)
    if isinstance(value, (int, float, bool, type(None), Decimal, date, datetime)):
        return value
    if isinstance(value, dict):
        return {
            _screen_untrusted_result(k, source_uri=source_uri, _depth=_depth + 1):
            _screen_untrusted_result(v, source_uri=source_uri, _depth=_depth + 1)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        screened = [
            _screen_untrusted_result(v, source_uri=source_uri, _depth=_depth + 1) for v in value
        ]
        return type(value)(screened) if not isinstance(value, set) else set(screened)
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
