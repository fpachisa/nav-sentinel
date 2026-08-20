"""Governance vocabulary owned by the control plane.

This module imports nothing from the rest of the package. That is deliberate and load-bearing:
`ApprovalClass` lived in the fund-accounting domain, which meant the control plane imported a
domain type to reason about four-eyes approval — a governance concept that has nothing to do
with funds. Placing it in a leaf module removes the import and makes the cycle impossible.

`CaseFacts` is the seam. The control plane previously took an `ExceptionCase` and read eleven
of its members, four of them irreducibly fund-accounting (`fund_id`, `breaks`, and three domain
enums consumed as `.value` with no import at all). A Protocol over that surface would have
restated the coupling rather than removed it, so instead the process hands over a flat value of
primitives and control-plane types, and the control plane reads nothing else.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ApprovalClass(StrEnum):
    """Who must sign off. Derived by the control plane from a unit-tagged magnitude and the
    tenant's thresholds — never declared by the agent or the process."""

    AUTO_CLEAR = "auto_clear"
    SINGLE_REVIEWER = "single_reviewer"
    FOUR_EYES = "four_eyes"
    CIO_ESCALATION = "cio_escalation"


class Effect(StrEnum):
    ALLOW = "allow"
    DENY = "deny"


class Impact(BaseModel):
    """A magnitude and the unit it is measured in.

    The unit travels with the number because processes do not share one. A NAV break is
    material in basis points; a share-register break is material in shares. The control plane
    never interprets the unit itself — it selects a threshold set by unit and compares.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    value: Decimal
    unit: str

    def __str__(self) -> str:
        return f"{self.value}{self.unit}" if self.unit == "bps" else f"{self.value} {self.unit}"


class ThresholdSet(BaseModel):
    """Materiality thresholds for one unit, owned by the control plane and resolved per tenant.

    Held here rather than in the process so that the band is *derived* rather than declared. A
    process-declared band would be caller-supplied governance — the same defect as a
    caller-supplied manifest, one level up.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    unit: str
    auto_clear_below: Decimal
    single_reviewer_below: Decimal
    four_eyes_below: Decimal


class CaseFacts(BaseModel):
    """Everything the control plane is permitted to know about a case.

    A pydantic model rather than a dataclass, specifically: pydantic coerces a `StrEnum` member
    to a plain `str`, so a process cannot smuggle a live domain enum in through `status` or
        `capability`. A frozen dataclass would pass one through intact with `.value` still
    resolving, satisfying every annotation and every static check while leaving the coupling in
    place.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str
    #: What the case is about, in the process's own identifier space — a fund, a share class, a
    #: legal entity. The control plane treats it as an opaque label.
    subject_id: str
    as_of: date
    #: Which capability this case needs, namespaced by process: "nav.fx_rate", "ta.subscription_in_transit".
    capability: str
    #: None when the process has not computed one yet. Distinct from zero, and banded
    #: fail-closed rather than auto-cleared.
    impact: Impact | None = None
    status: str
    severity: str | None = None
    item_count: int = 0
    #: Set by the process when the case must not clear on monetary materiality alone. A 2:1 stock
    #: split moves no money and still drives wrong dividend entitlement and a stock-record control
    #: failure. The domain refused to auto-clear it; the control plane did anyway, because nothing
    #: carried the signal across the seam -- so a floor enforced on one side of a boundary is not
    #: enforced.
    no_auto_clear: bool = False
    recurrence_key: str | None = None

    def as_span_attributes(self) -> dict[str, object]:
        """The span attributes for this case.

        Owned here rather than handed in by the process. A process-supplied mapping would put
        the attribute key names under process control, make the audit record non-uniform across
        processes — defeating the point of a shared governance log — and be invisible to both
        the import closure and the attribute scan.
        """
        attrs: dict[str, object] = {
            "nav.case.id": self.case_id,
            "nav.case.subject": self.subject_id,
            "nav.case.as_of": self.as_of.isoformat(),
            "nav.case.capability": self.capability,
            "nav.case.impact_value": str(self.impact.value) if self.impact else "not_computed",
            "nav.case.impact_unit": self.impact.unit if self.impact else "",
            "nav.case.status": self.status,
            "nav.case.item_count": self.item_count,
        }
        if self.severity:
            attrs["nav.case.severity"] = self.severity
        if self.recurrence_key:
            attrs["nav.case.recurrence_key"] = self.recurrence_key
        return attrs


class PolicyDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    effect: Effect
    policy_id: str
    reason: str
    agent_ref: str | None = None
    resource: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.effect is Effect.ALLOW

    def as_span_attributes(self) -> dict[str, str]:
        """Attributes for the decision event on the enclosing case span.

        `agent_ref` and `resource` are optional because a routing decision belongs to a case
        rather than to an agent, and an unknown-tool refusal has no resource that resolves. An
        absent key is omitted rather than stamped empty, so a reader can tell "not applicable"
        from "blank".
        """
        attrs = {
            "nav.policy.effect": self.effect.value,
            "nav.policy.id": self.policy_id,
            "nav.policy.reason": self.reason,
        }
        if self.agent_ref:
            attrs["nav.agent.ref"] = self.agent_ref
        if self.resource:
            attrs["nav.policy.resource"] = self.resource
        attrs.update({f"nav.policy.{k}": v for k, v in self.metadata.items()})
        return attrs


class PolicyViolation(RuntimeError):
    """A policy denied the action. Never a warning."""

    def __init__(self, decision: PolicyDecision) -> None:
        super().__init__(f"[{decision.policy_id}] {decision.reason}")
        self.decision = decision
