"""Policy definitions enforced by the Agent Gateway.

Policies are expressed here, once, as data and pure functions. No agent contains its own
copy of a control, because a control an agent can restate is a control an agent can
misstate. The gateway is the only place a decision is made.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel

from nav_sentinel.domain.models import ApprovalClass, ExceptionCase
from nav_sentinel.registry.models import AgentManifest


class Effect(StrEnum):
    ALLOW = "allow"
    DENY = "deny"


class PolicyDecision(BaseModel):
    effect: Effect
    policy_id: str
    reason: str
    agent_ref: str
    resource: str

    @property
    def allowed(self) -> bool:
        return self.effect is Effect.ALLOW

    def as_span_attributes(self) -> dict[str, str]:
        return {
            "nav.policy.effect": self.effect.value,
            "nav.policy.id": self.policy_id,
            "nav.policy.reason": self.reason,
            "nav.agent.ref": self.agent_ref,
            "nav.policy.resource": self.resource,
        }


class PolicyViolation(RuntimeError):
    def __init__(self, decision: PolicyDecision) -> None:
        super().__init__(f"[{decision.policy_id}] {decision.reason}")
        self.decision = decision


# --------------------------------------------------------------------------- policies


def tool_allowed(manifest: AgentManifest, tool_name: str) -> PolicyDecision:
    """P-001: an agent may call only the tools declared in its registry manifest."""
    if tool_name in manifest.allowed_tools:
        return PolicyDecision(
            effect=Effect.ALLOW,
            policy_id="P-001-TOOL-ALLOWLIST",
            reason=f"{tool_name} is declared in the manifest for {manifest.ref}",
            agent_ref=manifest.ref,
            resource=tool_name,
        )
    return PolicyDecision(
        effect=Effect.DENY,
        policy_id="P-001-TOOL-ALLOWLIST",
        reason=(
            f"{tool_name} is not in the allowed_tools of {manifest.ref}. "
            "Grant it in the registry manifest and republish; it cannot be granted at runtime."
        ),
        agent_ref=manifest.ref,
        resource=tool_name,
    )


def tool_within_data_scope(manifest: AgentManifest, tool_name: str,
                           reads: tuple[str, ...]) -> PolicyDecision:
    """P-006: a tool may only read data domains the agent's manifest declares.

    Without this, `data_scopes` was documentation. The manifests declared it, `bootstrap.sh`
    read it to decide IAM roles, and no runtime check consulted it -- so an agent scoped to
    positions could read the cash ledger through any tool it happened to be granted.
    """
    undeclared = [d for d in reads if d not in manifest.data_scopes.read]
    if not undeclared:
        return PolicyDecision(
            effect=Effect.ALLOW,
            policy_id="P-006-DATA-SCOPE",
            reason=f"{tool_name} reads {list(reads) or 'no internal domain'}, within scope",
            agent_ref=manifest.ref,
            resource=tool_name,
        )
    return PolicyDecision(
        effect=Effect.DENY,
        policy_id="P-006-DATA-SCOPE",
        reason=(
            f"{tool_name} reads {undeclared}, which {manifest.ref} does not declare in "
            f"data_scopes.read ({manifest.data_scopes.read})."
        ),
        agent_ref=manifest.ref,
        resource=tool_name,
    )


def may_propose_remediation(manifest: AgentManifest) -> PolicyDecision:
    """P-002: only an agent whose manifest grants drafting authority may draft an entry."""
    if manifest.authority.may_propose_remediation:
        return PolicyDecision(
            effect=Effect.ALLOW,
            policy_id="P-002-DRAFT-AUTHORITY",
            reason=f"{manifest.ref} holds drafting authority",
            agent_ref=manifest.ref,
            resource="remediation_proposal",
        )
    return PolicyDecision(
        effect=Effect.DENY,
        policy_id="P-002-DRAFT-AUTHORITY",
        reason=(
            f"{manifest.ref} is an investigator and may not draft accounting entries. "
            "Investigators report a root cause; remediation-agent drafts."
        ),
        agent_ref=manifest.ref,
        resource="remediation_proposal",
    )


def may_post_entry(
    manifest: AgentManifest, case: ExceptionCase, human_approval_ref: str | None
) -> PolicyDecision:
    """P-003: nothing posts to the books without a recorded human approval.

    The hard control. `max_autonomous_bps` is zero for every published agent, so this
    denies on authority before it ever reaches the approval check -- but both are evaluated
    so that the audit record states exactly which condition failed.
    """
    if not manifest.authority.may_post_entries:
        return PolicyDecision(
            effect=Effect.DENY,
            policy_id="P-003-NO-AUTONOMOUS-POSTING",
            reason=(
                f"{manifest.ref} has may_post_entries=false. No agent in this fleet holds "
                "posting authority; entries reach the ledger only through the approval queue."
            ),
            agent_ref=manifest.ref,
            resource=f"ledger:{case.case_id}",
        )
    if human_approval_ref is None:
        return PolicyDecision(
            effect=Effect.DENY,
            policy_id="P-003-NO-AUTONOMOUS-POSTING",
            reason="No human approval reference recorded against this case.",
            agent_ref=manifest.ref,
            resource=f"ledger:{case.case_id}",
        )
    return PolicyDecision(
        effect=Effect.ALLOW,
        policy_id="P-003-NO-AUTONOMOUS-POSTING",
        reason=f"Human approval {human_approval_ref} recorded.",
        agent_ref=manifest.ref,
        resource=f"ledger:{case.case_id}",
    )


def approval_route(case: ExceptionCase) -> PolicyDecision:
    """P-004: materiality determines who must sign off. Computed, never inferred."""
    cls = case.approval_class or ApprovalClass.CIO_ESCALATION
    bps = case.nav_impact_bps or 0.0
    return PolicyDecision(
        effect=Effect.ALLOW,
        policy_id="P-004-MATERIALITY-ROUTING",
        reason=f"{bps:.4f}bps of NAV routes to {cls.value}",
        agent_ref="agent-gateway",
        resource=f"case:{case.case_id}",
    )


def untrusted_ingest_requires_armor(manifest: AgentManifest) -> PolicyDecision:
    """P-005: an agent that ingests external content must screen it through Model Armor."""
    if not manifest.untrusted_inputs:
        return PolicyDecision(
            effect=Effect.ALLOW,
            policy_id="P-005-UNTRUSTED-INGEST",
            reason=f"{manifest.ref} declares no untrusted inputs",
            agent_ref=manifest.ref,
            resource="external_content",
        )
    if manifest.requires_model_armor:
        return PolicyDecision(
            effect=Effect.ALLOW,
            policy_id="P-005-UNTRUSTED-INGEST",
            reason=f"{manifest.ref} ingests untrusted content and Model Armor is required",
            agent_ref=manifest.ref,
            resource="external_content",
        )
    return PolicyDecision(
        effect=Effect.DENY,
        policy_id="P-005-UNTRUSTED-INGEST",
        reason=(
            f"{manifest.ref} declares untrusted_inputs=true but requires_model_armor=false. "
            "An agent that reads the public internet cannot opt out of screening."
        ),
        agent_ref=manifest.ref,
        resource="external_content",
    )
