"""The Agent Gateway: the single enforcement point.

Every tool call, every drafting attempt and every posting attempt passes through here. The
gateway reads the calling agent's registry manifest, evaluates policy, records the decision
on the trace, and either proceeds or refuses.

Enforcement lives outside the agents on purpose. An agent that checks its own permissions is
one prompt away from deciding it has them.
"""

from __future__ import annotations

from typing import Any, Callable, TypeVar

from nav_sentinel.control_plane import identity, policies, telemetry
from nav_sentinel.control_plane.policies import Effect, PolicyDecision, PolicyViolation
from nav_sentinel.domain.models import ExceptionCase
from nav_sentinel.registry.models import AgentManifest

T = TypeVar("T")

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


def call_tool(tool_name: str, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Invoke a tool on behalf of the currently bound agent identity.

    Raises IdentityError if no identity is bound, and PolicyViolation if the tool is not
    declared in that agent's manifest. Both are failures, not warnings.
    """
    manifest = identity.current()
    _enforce(policies.tool_allowed(manifest, tool_name))

    with telemetry.span(
        "gateway.tool_call",
        **{
            "nav.agent.ref": manifest.ref,
            "nav.agent.service_account": identity.service_account_email(manifest),
            "nav.tool.name": tool_name,
        },
    ):
        return fn(*args, **kwargs)


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


def admit_untrusted_content(
    manifest: AgentManifest, text: str, *, source_uri: str | None = None
) -> str:
    """Screen external content before it reaches a model context.

    Enforces P-005 first: an agent that declares untrusted inputs but no screening
    requirement is refused outright, before any content is fetched into context.
    """
    from nav_sentinel.control_plane import model_armor

    _enforce(policies.untrusted_ingest_requires_armor(manifest))

    if not manifest.untrusted_inputs:
        return text

    with telemetry.span(
        "gateway.model_armor_screen",
        **{
            "nav.agent.ref": manifest.ref,
            "nav.armor.template": model_armor.template_path(),
            "nav.armor.source_uri": source_uri or "",
            "nav.armor.content_bytes": len(text.encode("utf-8")),
        },
    ) as sp:
        verdict = model_armor.screen(text, source_uri=source_uri)
        sp.set_attribute("nav.armor.verdict", verdict.verdict)
        sp.set_attribute("nav.armor.blocked", verdict.blocked)
        telemetry.record_evidence(
            sp, source=source_uri or "external", summary=verdict.summary,
            trusted=False, armor_verdict=verdict.verdict,
        )
        return text
