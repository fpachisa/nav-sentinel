"""What an investigator is allowed to return, and what it is allowed to say.

An investigator explains *why* a break happened. It does not propose a correction, and it cannot:
`Verdict` has no field capable of carrying one, so S4's drafting cannot leak backwards into the
investigation layer. That is P-002 expressed in the type system as well as in a policy check.

The narrower claim matters, because the broader one is false. `reasoning` and `unresolved` are free
text, and a model can certainly write "post DR cash 1,234 / CR dividend income 1,234" into either.
What is guaranteed is that no *structured* proposal can cross this boundary, and that **S4 reads
only the typed fields** -- there is a test for that, because a docstring saying so is worth nothing.

The other rule this module enforces is that a citation is bound to an observed fact rather than to
a plausible sentence. A model does not construct an `EvidenceItem`; it names an `observation_id`,
and `evidence_from()` builds the item from what the tool actually returned. An investigator that
cites an ECB rate it never fetched has nothing to cite with.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, model_validator

from nav_sentinel.control_plane.observations import Observation, utcnow
from nav_sentinel.domain.models import (
    BreakCategory,
    EvidenceItem,
    ObservedFacts,
    RootCauseHypothesis,
)

__all__ = [
    "UNKNOWN",
    "Citation",
    "Observation",
    "UnknownObservation",
    "Verdict",
    "category_for",
    "evidence_from",
    "refusal",
    "resolve_citations",
    "utcnow",
]

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Mapping

#: What a verdict says when it could not reach one. Distinct from a low-confidence answer: this
#: asserts nothing, so §1's "every verdict cites evidence" is scoped to the ones that do.
UNKNOWN = "UNKNOWN"


class Citation(BaseModel):
    """All a model is permitted to say about its evidence: which observation, and why it matters.

    It cannot supply `source_uri`, `retrieved_at` or any observed value. Those are looked up. An
    earlier design had the model cite `(tool, digest)`, which authorised any claim about the result
    of a call the model itself chose -- a real call vouching for invented numbers.
    """

    model_config = ConfigDict(extra="forbid")

    observation_id: str
    relevance: str = Field(min_length=1, max_length=400)


class Verdict(BaseModel):
    """An investigator's finding on one case.

    No remediation field, by construction. See the module docstring for the exact scope of that
    claim.
    """

    model_config = ConfigDict(extra="forbid")

    case_id: str
    capability: str
    root_cause: str = Field(min_length=1, max_length=1_000)
    confidence: float = Field(ge=0.0, le=1.0)
    citations: list[Citation] = Field(default_factory=list)
    reasoning: str = Field(default="", max_length=4_000)
    unresolved: str = ""

    @model_validator(mode="after")
    def _an_asserted_cause_must_cite_something(self) -> Verdict:
        """A confident answer with no evidence is the failure mode worth refusing outright.

        Scoped to verdicts that assert a cause: a refusal asserts none, so requiring citations of
        it would make the refusal path unrepresentable -- which is how an earlier draft of this
        contract contradicted the criterion it was written to satisfy.
        """
        if self.root_cause != UNKNOWN and not self.citations:
            raise ValueError(
                f"a verdict asserting {self.root_cause[:60]!r} must cite at least one observation; "
                f"return root_cause={UNKNOWN!r} if the evidence does not support a cause"
            )
        if self.root_cause == UNKNOWN and self.confidence > 0.5:
            raise ValueError(
                f"confidence {self.confidence} on an UNKNOWN root cause. A verdict that asserts "
                f"nothing cannot be held confidently."
            )
        return self

    @property
    def asserts_a_cause(self) -> bool:
        return self.root_cause != UNKNOWN

    def to_hypothesis(
        self, observations: Mapping[str, Observation], *, agent_ref: str
    ) -> RootCauseHypothesis:
        """Convert to the domain type `ExceptionCase.hypotheses` already holds.

        Every `EvidenceItem` is built here, from the recorded observation. Nothing the model wrote
        reaches `source_uri`, `retrieved_at` or `observed`.
        """
        agent, _, version = agent_ref.partition("@")
        return RootCauseHypothesis(
            category=category_for(self.capability),
            statement=self.root_cause,
            confidence=self.confidence,
            evidence=[evidence_from(observations[c.observation_id], c) for c in self.citations],
            investigator_agent=agent,
            investigator_version=version or None,
        )


def evidence_from(observation: Observation, citation: Citation) -> EvidenceItem:
    """Build the citation's evidence item from the observation, not from the model's words.

    The model contributes exactly one thing -- `relevance`, why this observation bears on the case.
    Everything a downstream check tests is copied from what the tool returned.
    """
    return EvidenceItem(
        source=observation.source,
        source_uri=observation.source_uri,
        retrieved_at=observation.retrieved_at,
        summary=citation.relevance,
        trusted=observation.trusted,
        armor_verdict=observation.armor_verdict,
        tool=observation.tool,
        observed=ObservedFacts.from_recorded(observation.observed),
    )


class UnknownObservation(LookupError):
    """A verdict cited an observation that this case never recorded."""


def resolve_citations(
    verdict: Verdict, observations: Mapping[str, Observation]
) -> list[Observation]:
    """The observations a verdict cites, or refuse.

    Two ways to refuse, and the second is the one that matters: an id nobody recorded, and an id
    recorded **for a different case**. Without the second check an investigator could cite a real
    rate lookup performed while working an unrelated break -- a genuine call vouching for an
    unrelated claim, which is the hole this whole mechanism exists to close.
    """
    resolved = []
    for citation in verdict.citations:
        observation = observations.get(citation.observation_id)
        if observation is None:
            raise UnknownObservation(
                f"verdict on {verdict.case_id} cites {citation.observation_id}, which was never "
                f"recorded. Evidence must come from a tool call this investigation actually made."
            )
        if observation.case_id != verdict.case_id:
            raise UnknownObservation(
                f"verdict on {verdict.case_id} cites {citation.observation_id}, recorded on case "
                f"{observation.case_id}. A tool call made for another case is not evidence here."
            )
        resolved.append(observation)
    return resolved


def category_for(capability: str) -> BreakCategory:
    """`nav.fx_rate` -> `BreakCategory.FX_RATE`, refusing anything outside the enum.

    `ExceptionCase.category` is a closed enum while a capability is a namespaced string, so the
    conversion has to be total and has to refuse rather than default. Defaulting an unrecognised
    capability to UNCLASSIFIED would turn a routing bug into a silently mis-filed case.
    """
    _, _, bare = capability.rpartition(".")
    try:
        return BreakCategory(bare)
    except ValueError as exc:
        raise ValueError(
            f"{capability!r} does not name a break category. Valid: "
            f"{sorted(c.value for c in BreakCategory)}"
        ) from exc


def refusal(
    case_id: str, capability: str, *, reason: str, evidence: Observation | None = None
) -> Verdict:
    """The verdict an investigator returns when its evidence was refused.

    A refusal, not an exception. The poisoned corporate-action notice is *designed* to be refused,
    so letting `ContentBlocked` propagate would make the project's centrepiece control surface as a
    stack trace on camera. `reason` is built from the actual failure rather than a fixed string:
    an earlier draft hardcoded a P-005 label, which would have described an allowlist denial as a
    screening block.
    """
    return Verdict(
        case_id=case_id,
        capability=capability,
        root_cause=UNKNOWN,
        confidence=0.0,
        citations=[Citation(observation_id=evidence.observation_id, relevance=reason)]
        if evidence is not None
        else [],
        unresolved=reason,
    )
