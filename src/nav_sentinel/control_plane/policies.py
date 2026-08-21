"""Policy definitions enforced by the Agent Gateway.

Policies are expressed here, once, as data and pure functions. No agent contains its own
copy of a control, because a control an agent can restate is a control an agent can
misstate. The gateway is the only place a decision is made.
"""

from __future__ import annotations

from nav_sentinel.control_plane import approvals
from nav_sentinel.control_plane.governance import (
    ApprovalClass,
    CaseFacts,
    Effect,
    Impact,
    PolicyDecision,
    PolicyViolation,
    ThresholdSet,
)
from nav_sentinel.control_plane.packs import thresholds_for
from nav_sentinel.registry.models import AgentManifest

__all__ = [
    "ApprovalClass",
    "CaseFacts",
    "Effect",
    "Impact",
    "PolicyDecision",
    "PolicyViolation",
    "ThresholdSet",
    "approval_route",
    "band_for",
    "may_post_entry",
    "may_propose_remediation",
    "tool_allowed",
    "tool_within_data_scope",
    "untrusted_ingest_requires_armor",
]


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


def may_post_entry(  # noqa: PLR0911 -- one return per denial reason; the audit record must
    #                     state exactly which condition failed, and collapsing them into a
    #                     single exit would lose that.
    manifest: AgentManifest, facts: CaseFacts, human_approval_ref: str | None,
    thresholds: ThresholdSet | None = None,
) -> PolicyDecision:
    """P-003: nothing posts to the books without a recorded human approval.

    The hard control. No published agent holds posting authority, so this denies on authority
    before reaching the approval check -- but every condition is evaluated so the audit record
    states exactly which one failed.

    The authority ceiling is consulted here. It previously existed in the manifest schema, was
    asserted to be zero by a test, and was read by no policy at all: declarative rather than
    enforced.
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
            resource=f"ledger:{facts.case_id}",
        )
    if human_approval_ref is None:
        # The only route to the ledger without a human is a case the control plane itself bands
        # as AUTO_CLEAR *and* which sits inside the agent's declared ceiling. Both, not either:
        # consulting the ceiling alone let a manifest grant itself 500bps of headroom over a
        # case the control plane had banded cio_escalation, and the permissive control won.
        band = band_for(facts.impact, thresholds, no_auto_clear=facts.no_auto_clear)
        if band is ApprovalClass.AUTO_CLEAR and manifest.authority.within_ceiling(facts.impact):
            return PolicyDecision(
                effect=Effect.ALLOW,
                policy_id="P-003-NO-AUTONOMOUS-POSTING",
                reason=(
                    f"{facts.impact} bands to {band.value} and sits within {manifest.ref}'s "
                    f"ceiling of {manifest.authority.max_autonomous_impact}."
                ),
                agent_ref=manifest.ref,
                resource=f"ledger:{facts.case_id}",
                metadata={"band": band.value},
            )
        return PolicyDecision(
            effect=Effect.DENY,
            policy_id="P-003-NO-AUTONOMOUS-POSTING",
            reason=(
                f"No human approval recorded. {facts.impact} bands to {band.value}"
                + ("" if manifest.authority.max_autonomous_impact is None
                   else f" and {manifest.ref}'s ceiling is "
                        f"{manifest.authority.max_autonomous_impact}")
                + "."
            ),
            agent_ref=manifest.ref,
            resource=f"ledger:{facts.case_id}",
            metadata={"band": band.value},
        )
    # The reference is resolved, not believed. It was accepted as a bare string, so an agent
    # could invent one: `human_approval_ref="APPR-anything"` returned ALLOW against a record
    # that did not exist.
    record = approvals.resolve(human_approval_ref)
    if record is None:
        return PolicyDecision(
            effect=Effect.DENY,
            policy_id="P-003-NO-AUTONOMOUS-POSTING",
            reason=(
                f"approval reference {human_approval_ref!r} does not resolve to a recorded "
                f"approval."
            ),
            agent_ref=manifest.ref,
            resource=f"ledger:{facts.case_id}",
        )
    if record.case_id != facts.case_id:
        return PolicyDecision(
            effect=Effect.DENY,
            policy_id="P-003-NO-AUTONOMOUS-POSTING",
            reason=(
                f"approval {record.ref} was granted for case {record.case_id}, not "
                f"{facts.case_id}."
            ),
            agent_ref=manifest.ref,
            resource=f"ledger:{facts.case_id}",
        )

    band = band_for(facts.impact, thresholds, no_auto_clear=facts.no_auto_clear)
    satisfied, why = record.satisfies(band)
    if not satisfied:
        return PolicyDecision(
            effect=Effect.DENY,
            policy_id="P-003-NO-AUTONOMOUS-POSTING",
            reason=why,
            agent_ref=manifest.ref,
            resource=f"ledger:{facts.case_id}",
            metadata={"band": band.value, "granted_band": record.granted_band.value},
        )
    return PolicyDecision(
        effect=Effect.ALLOW,
        policy_id="P-003-NO-AUTONOMOUS-POSTING",
        reason=why,
        agent_ref=manifest.ref,
        resource=f"ledger:{facts.case_id}",
        metadata={"band": band.value, "approvers": ",".join(record.approvers)},
    )


def band_for(
    impact: Impact | None,
    thresholds: ThresholdSet | None = None,
    *,
    no_auto_clear: bool = False,
) -> ApprovalClass:
    """Derive who must approve, from a unit-tagged magnitude and the tenant's thresholds.

    The control plane derives the band; the process supplies only the magnitude and its unit.
    Accepting a process-computed band would be caller-supplied governance — the same defect as a
    caller-supplied manifest, one level up — and it is what the previous implementation did:
    P-004 documented itself as "computed, never inferred" while reading a field the domain had
    already set.

    An unrecognised unit escalates. A process measuring impact in something the control plane
    has no thresholds for cannot be auto-cleared on the strength of that.
    """
    if impact is None:
        # An uncomputed magnitude escalates. Collapsing it to zero made every untriaged case --
        # the default state of a freshly opened one -- band as auto_clear on an impact nobody
        # had calculated. The unknown-unit rule below and this one are the same rule.
        return ApprovalClass.CIO_ESCALATION

    thresholds = thresholds or thresholds_for(impact.unit)
    if thresholds is None:
        return ApprovalClass.CIO_ESCALATION

    magnitude = abs(impact.value)
    if magnitude < thresholds.auto_clear_below:
        # A case flagged no_auto_clear floors at single reviewer however small its monetary
        # impact. The flag comes from the process, which is the only side that knows *why* a
        # zero-value difference still needs a human.
        return ApprovalClass.SINGLE_REVIEWER if no_auto_clear else ApprovalClass.AUTO_CLEAR
    if magnitude < thresholds.single_reviewer_below:
        return ApprovalClass.SINGLE_REVIEWER
    if magnitude < thresholds.four_eyes_below:
        return ApprovalClass.FOUR_EYES
    return ApprovalClass.CIO_ESCALATION


def delegation(
    caller_ref: str,
    capability: str,
    *,
    permitted: tuple[str, ...],
    depth: int,
    max_depth: int,
) -> PolicyDecision:
    """P-009: may this agent ask another agent to do something.

    Two refusals, and they guard different things.

    **An undeclared capability.** An agent may request only what its *process* declares it may
    request. Declared on the pack rather than in the agent's manifest for a reason beyond the
    seam: delegation is a statement about how two processes are allowed to interact, and putting
    it in the manifest would let one agent's document widen the coupling between departments.

    **Depth.** An agent that can delegate to an agent that can delegate is a loop, and a loop
    inside a model's tool surface is an unbounded bill. Bounded rather than detected, because
    cycle detection answers "is this call currently looping" and the question that matters is
    "could it".

    What this policy does *not* do is lend privileges. The sub-agent is resolved from the
    published registry and bound to its own identity, so its allowlist and data scopes are its
    own; a caller cannot pass its own along. That is enforced by `identity.acting_as` and the
    ordinary P-001 and P-006 checks, not here -- this decides only whether the request may be
    made at all.
    """
    if depth >= max_depth:
        return PolicyDecision(
            effect=Effect.DENY,
            policy_id="P-009-DELEGATION",
            reason=(
                f"delegation depth {depth + 1} exceeds the limit of {max_depth}. An agent that "
                f"delegates to an agent that delegates is a loop with a model in it."
            ),
            agent_ref=caller_ref,
            resource=capability,
            metadata={"depth": str(depth + 1), "max_depth": str(max_depth)},
        )
    if capability not in permitted:
        return PolicyDecision(
            effect=Effect.DENY,
            policy_id="P-009-DELEGATION",
            reason=(
                f"{caller_ref} may not request {capability!r}. Its process declares "
                f"{list(permitted) or 'no delegations'}."
            ),
            agent_ref=caller_ref,
            resource=capability,
            metadata={"declared": ",".join(permitted)},
        )
    return PolicyDecision(
        effect=Effect.ALLOW,
        policy_id="P-009-DELEGATION",
        reason=f"{caller_ref} may request {capability!r} at depth {depth + 1}",
        agent_ref=caller_ref,
        resource=capability,
        metadata={"depth": str(depth + 1), "max_depth": str(max_depth)},
    )


def stage_transition(
    case_id: str, frm: str | None, to: str, *, allowed: bool, reason: str
) -> PolicyDecision:
    """P-008: may this case move to that stage.

    Numbered alongside the others rather than folded into P-004. Approval *routing* decides who must
    sign; this decides whether the case is even at a point where signing is the next thing that can
    happen. Compensation before approval is refused here, and P-004 has nothing to say about it.

    A denial is a decision, so it is returned and recorded like any other. A rejected external event
    that left no trace would be indistinguishable from an event that never arrived, and "this
    delivery was refused, for this reason" is exactly what a reviewer of a stalled case needs.
    """
    return PolicyDecision(
        effect=Effect.ALLOW if allowed else Effect.DENY,
        policy_id="P-008-STAGE-TRANSITION",
        reason=reason,
        resource=case_id,
        metadata={"from": frm or "not_opened", "to": to},
    )


def approval_route(facts: CaseFacts, thresholds: ThresholdSet | None = None) -> PolicyDecision:
    """P-004: route a case to its approval class.

    Always an ALLOW: routing is not a permission question, it is a statement of who must sign.
    The decision is recorded so the audit trail carries the band and the magnitude that produced
    it.
    """
    band = band_for(facts.impact, thresholds, no_auto_clear=facts.no_auto_clear)
    return PolicyDecision(
        effect=Effect.ALLOW,
        policy_id="P-004-APPROVAL-ROUTE",
        reason=f"{facts.impact} routes to {band.value}",
        resource=facts.case_id,
        metadata={
            "band": band.value,
            # `impact` is None on every freshly opened case, which is the state `band_for`
            # deliberately escalates. Dereferencing it here raised AttributeError instead, so a
            # case could not have its trace opened before triage and no P-004 record reached the
            # governance log.
            "impact_value": str(facts.impact.value) if facts.impact else "not_computed",
            "impact_unit": facts.impact.unit if facts.impact else "",
            "capability": facts.capability,
        },
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


def verdict_is_corroborated(
    manifest: AgentManifest,
    capability: str,
    cited_facts: frozenset[str],
    required: tuple[str, ...],
) -> PolicyDecision:
    """P-007: an agent may not assert a root cause without the corroboration its process demands.

    An FX verdict resting only on our own books has not explained anything -- it has restated the
    disagreement. The requirement is declared per capability by the process (`ProcessPack.
    evidence_requirements`) and evaluated here, so a second process states its own and inherits the
    check without touching this function.

    **Facts, not tool namespaces.** Requiring a namespace only asked that *some* call to it had
    happened: measured, a GBP rate lookup for an unrelated July date that returned nothing satisfied
    an `ecb_fx` requirement, while every number in the verdict's root cause was invented. The facts
    come from the cited observations' own projections, so a call that returned nothing contributes
    nothing and cannot corroborate anything.

    Takes fact names and a requirement as plain strings, never a verdict: the control plane must not
    know what a verdict is, and this keeps the check on the platform side of the seam where the
    governance log lives.

    Deliberately a policy rather than a validation. It decides whether an agent's output may be
    acted on, it belongs in the decision log the exception console renders, and a reviewer asking
    "why was this verdict rejected?" should find the answer in the same place as every other denial.
    """
    missing = sorted(set(required) - cited_facts)
    if not missing:
        return PolicyDecision(
            effect=Effect.ALLOW,
            policy_id="P-007-EVIDENCE-CORROBORATION",
            reason=(
                f"verdict on {capability} cites {sorted(cited_facts) or 'no observed fact'}"
                + (f", satisfying {list(required)}" if required else " and none is required")
            ),
            agent_ref=manifest.ref,
            resource=capability,
        )
    return PolicyDecision(
        effect=Effect.DENY,
        policy_id="P-007-EVIDENCE-CORROBORATION",
        reason=(
            f"verdict on {capability} cites no observation carrying {missing}. {manifest.ref} may "
            f"not assert a cause without them; it cites {sorted(cited_facts) or 'no fact at all'}."
        ),
        agent_ref=manifest.ref,
        resource=capability,
    )
