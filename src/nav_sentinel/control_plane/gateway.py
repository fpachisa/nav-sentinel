"""The Agent Gateway: the single enforcement point.

Every tool call, every drafting attempt and every posting attempt passes through here. The
gateway reads the calling agent's registry manifest, evaluates policy, records the decision
on the trace, and either proceeds or refuses.

Enforcement lives outside the agents on purpose. An agent that checks its own permissions is
one prompt away from deciding it has them.
"""

from __future__ import annotations

from typing import Any

from nav_sentinel.control_plane import identity, policies, telemetry
from nav_sentinel.tools import catalogue
from nav_sentinel.control_plane.policies import Effect, PolicyDecision, PolicyViolation
from nav_sentinel.domain.models import ExceptionCase
from nav_sentinel.registry.models import AgentManifest

# Every decision, in order, for the life of the process. The exception console renders this
# as the governance log and the demo reads from it directly.
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
    spec = catalogue.resolve(tool_name)

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

    _enforce(policies.tool_allowed(manifest, tool_name))
    _enforce(policies.tool_within_data_scope(manifest, tool_name, spec.reads))

    with telemetry.span(
        "gateway.tool_call",
        **{
            "nav.agent.ref": manifest.ref,
            "nav.agent.service_account": identity.service_account_email(manifest),
            "nav.tool.name": tool_name,
            "nav.tool.reads": list(spec.reads),
            "nav.tool.untrusted_output": spec.untrusted_output,
        },
    ):
        result = spec.fn(*args, **kwargs)

    # Screening is bound to the tool that fetched the bytes, not to an agent's self-declared
    # flag. An agent cannot forget to screen, because it never had the option.
    if spec.untrusted_output and isinstance(result, str):
        return admit_untrusted_content(result, source_uri=f"tool:{tool_name}")
    return result


def authorize_drafting(manifest: AgentManifest) -> PolicyDecision:
    return _enforce(policies.may_propose_remediation(manifest))


def authorize_posting(
    manifest: AgentManifest, case: ExceptionCase, human_approval_ref: str | None = None
) -> PolicyDecision:
    """Always evaluated before any write to the ledger. With the fleet as published, this
    denies unconditionally -- which is the intended behaviour, not a limitation."""
    return _enforce(policies.may_post_entry(manifest, case, human_approval_ref))


def route_for_approval(case: ExceptionCase) -> PolicyDecision:
    return _record(policies.approval_route(case))


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
