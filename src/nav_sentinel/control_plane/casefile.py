"""A case that outlives the request that opened it.

Every case in this system has so far been detected, worked and finished inside one invocation. That
is fine for a reconciliation break and useless for anything a regulator would recognise as a
remediation: a published NAV error runs for weeks, crosses departments, and waits on events nobody
in the process controls. `ExceptionStatus` has carried ten values including `AWAITING_APPROVAL`
since early in this build, and **nothing ever transitioned between them** -- the enum was a
description of an intention.

**The mechanics here are platform; the stages are the process's.** This module validates a
transition against a graph the *pack* declares, exactly as `band_for` derives a band from
thresholds a pack declares. It knows nothing about NAVs, investors or regulators, and a second
process wanting a different lifecycle ships a different graph rather than changing this file.

Three properties, each of which exists because its absence is a defect this build has already made
somewhere else:

**A transition is validated, never assumed.** An unknown stage raises. An edge that is not in the
graph raises. There is no default branch that lets an unrecognised transition through as a no-op,
because a state machine whose illegal moves are silent is a suggestion.

**History is append-only, and position is the concurrency control.** `Repository.save_case`
overwrites, so the current stage alone cannot answer "who moved this case, when, on what evidence".
The stage entry is keyed by `(case_id, sequence)`, so two deliveries of the same event collide
rather than both advancing the case -- and Pub/Sub is at-least-once, so that is a live concern and
not a hypothetical.

**Every transition records a policy decision.** The audit trail is the deliverable. A stage change
that leaves no governance record is exactly the kind of state change this project exists to make
impossible.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from nav_sentinel.control_plane import gateway
from nav_sentinel.control_plane.observations import utcnow

if TYPE_CHECKING:  # pragma: no cover
    from nav_sentinel.control_plane.repository import Repository

class UnknownStage(ValueError):
    """A stage the declaring process never declared."""


class IllegalTransition(ValueError):
    """An edge that is not in the declared graph.

    Raised rather than warned. The case this protects is compensation before approval: a move that
    is individually plausible, arrives as a well-formed external event, and must not happen.
    """


@dataclass(frozen=True)
class Lifecycle:
    """The stages a process declares, and which moves between them are legal.

    Declared as explicit edges rather than a linear list. A remediation that can go from
    *materiality determined* either to *awaiting approval* or straight to *closed* (immaterial, no
    compensation due) is two edges from one stage, and a linear list cannot say that.
    """

    stages: tuple[str, ...]
    transitions: tuple[tuple[str, str], ...]
    initial: str
    terminal: tuple[str, ...]

    def __post_init__(self) -> None:
        unknown = {s for edge in self.transitions for s in edge} - set(self.stages)
        if unknown:
            raise UnknownStage(f"transitions reference undeclared stage(s): {sorted(unknown)}")
        if self.initial not in self.stages:
            raise UnknownStage(f"initial stage {self.initial!r} is not declared")
        undeclared_terminal = set(self.terminal) - set(self.stages)
        if undeclared_terminal:
            raise UnknownStage(f"terminal stage(s) not declared: {sorted(undeclared_terminal)}")
        # A terminal stage with an outbound edge is not terminal, and a non-terminal stage with no
        # outbound edge is a case that can never finish. Both are graph mistakes worth refusing at
        # construction rather than discovering when a case gets stuck in production.
        for stage in self.stages:
            outbound = [b for a, b in self.transitions if a == stage]
            if stage in self.terminal and outbound:
                raise IllegalTransition(
                    f"{stage!r} is declared terminal but has outbound edges to {outbound}"
                )
            if stage not in self.terminal and not outbound:
                raise IllegalTransition(
                    f"{stage!r} is not terminal and has no outbound edge, so a case reaching it "
                    f"can never progress or close"
                )

    def allows(self, frm: str, to: str) -> bool:
        return (frm, to) in self.transitions

    def next_stages(self, frm: str) -> tuple[str, ...]:
        return tuple(b for a, b in self.transitions if a == frm)


@dataclass(frozen=True)
class Casefile:
    """One case's position in its lifecycle, plus how it got there."""

    case_id: str
    stage: str
    sequence: int
    history: tuple[dict[str, Any], ...] = ()


def open_case(store: Repository, case_id: str, lifecycle: Lifecycle, *, note: str = "") -> Casefile:
    """Record a case's arrival at its initial stage."""
    entry = {
        "from": None,
        "to": lifecycle.initial,
        "recorded_at": utcnow().isoformat(),
        "note": note,
    }
    store.record_stage(case_id, 0, entry)
    gateway.record_stage_transition(case_id, None, lifecycle.initial, allowed=True, reason="case opened")
    return Casefile(case_id=case_id, stage=lifecycle.initial, sequence=0, history=(entry,))


def load(store: Repository, case_id: str) -> Casefile | None:
    """Rebuild a casefile from its recorded history, not from a field on a document.

    Deliberately derived. A `stage` field on the case document would be the fast path and would also
    be the thing that silently disagrees with the history beside it; deriving it means the two
    cannot drift, and the history is the record a reviewer actually needs.
    """
    history = store.stages_for(case_id)
    if not history:
        return None
    last = history[-1]
    return Casefile(
        case_id=case_id,
        stage=last["to"],
        sequence=last["sequence"],
        history=tuple(history),
    )


def advance(
    store: Repository,
    casefile: Casefile,
    to: str,
    lifecycle: Lifecycle,
    *,
    note: str = "",
    evidence: tuple[str, ...] = (),
) -> Casefile:
    """Move a case one stage, or refuse and say why.

    The refusal is recorded as a policy decision before the exception is raised. A denial that
    leaves no trace is indistinguishable from an event that never arrived, and "the event was
    rejected" is precisely what a reviewer of a stalled remediation needs to see.
    """
    if to not in lifecycle.stages:
        gateway.record_stage_transition(casefile.case_id, casefile.stage, to, allowed=False, reason="unknown stage")
        raise UnknownStage(
            f"{to!r} is not a stage this process declares. Declared: {list(lifecycle.stages)}"
        )
    if not lifecycle.allows(casefile.stage, to):
        reason = (
            f"{casefile.stage} -> {to} is not a declared transition; from {casefile.stage} the "
            f"case may only go to {list(lifecycle.next_stages(casefile.stage))}"
        )
        gateway.record_stage_transition(casefile.case_id, casefile.stage, to, allowed=False, reason=reason)
        raise IllegalTransition(reason)

    sequence = casefile.sequence + 1
    entry = {
        "from": casefile.stage,
        "to": to,
        "recorded_at": utcnow().isoformat(),
        "note": note,
        "evidence": list(evidence),
    }
    # Recorded before the decision is logged as allowed: if the append collides -- a repeated
    # delivery -- the case has not moved and no decision should claim it did.
    store.record_stage(casefile.case_id, sequence, entry)
    gateway.record_stage_transition(casefile.case_id, casefile.stage, to, allowed=True, reason="declared transition")
    return Casefile(
        case_id=casefile.case_id,
        stage=to,
        sequence=sequence,
        history=(*casefile.history, entry),
    )


