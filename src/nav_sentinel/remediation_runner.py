"""Walking a remediation case through its lifecycle, one delivered event at a time.

Outside both layers, beside `composition` and `ta_cli`, for the reason they are: this is wiring. The
`remediation_office` pack declares the lifecycle and the event vocabulary but may reach the platform
only through `packs`, `governance` and `gateway` -- so it cannot import `casefile` or `repository`,
and something entitled to know about both has to introduce them.

**Each call handles exactly one event and returns.** There is no loop, no sleep and nothing held
open between events. That is the whole point: the case's position lives in Firestore and nowhere
else, so the next event can be handled by a different process, on a different revision, weeks later.
If any of it were carried in memory, "multi-week" would be a description of a variable that happened
to stay in scope.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from nav_sentinel.control_plane import casefile
from nav_sentinel.control_plane.casefile import Casefile
from nav_sentinel.control_plane.governance import IllegalTransition, UnknownStage
from nav_sentinel.control_plane.repository import ImmutableRecord, Repository
from nav_sentinel.remediation_office import events
from nav_sentinel.remediation_office.lifecycle import AWAITING, REMEDIATION


class UnknownCase(ValueError):
    """An event for a case that was never opened."""


@dataclass(frozen=True)
class Applied:
    """What one delivered event did to one case."""

    case_id: str
    event: str
    stage: str
    #: What the case is now waiting for. The reason a nine-day-old case is legible at a glance.
    awaiting: str
    #: False when the event was a duplicate delivery. The case is untouched and that is success,
    #: not failure -- Pub/Sub is at-least-once and an idempotent no-op is the correct outcome.
    advanced: bool = True

    @property
    def closed(self) -> bool:
        return self.stage in REMEDIATION.terminal


def apply_event(store: Repository, payload: dict[str, Any]) -> Applied:
    """Apply one event to one case, or refuse and say why.

    Refusals are exceptions rather than return values because each has a different correct response
    from the caller: an unknown event or an illegal transition is permanently undeliverable, while a
    duplicate is a success that must not be retried. Collapsing them into a status field would put
    that decision in the caller's guesswork.
    """
    case_id = payload.get("case_id")
    if not isinstance(case_id, str) or not case_id:
        raise UnknownCase("event carries no case_id")

    event = payload.get("event")
    if not isinstance(event, str):
        raise events.UnknownEvent(f"event name is {type(event).__name__}, not a string")
    target = events.stage_for(event)

    existing = casefile.load(store, case_id)

    if event == events.OPENING_EVENT:
        if existing is not None:
            # A redelivered opening event. The case exists and is further along; re-opening it would
            # reset a case that may be weeks into compensation.
            return Applied(
                case_id=case_id,
                event=event,
                stage=existing.stage,
                awaiting=AWAITING.get(existing.stage, ""),
                advanced=False,
            )
        opened = casefile.open_case(
            store,
            case_id,
            REMEDIATION,
            note=str(payload.get("note", "")),
            occurred_on=str(payload.get("occurred_on", "")),
        )
        return Applied(
            case_id=case_id,
            event=event,
            stage=opened.stage,
            awaiting=AWAITING.get(opened.stage, ""),
        )

    if existing is None:
        raise UnknownCase(
            f"{case_id} has no recorded history, so {event!r} has nothing to advance. The opening "
            f"event is {events.OPENING_EVENT!r}."
        )

    if existing.stage == target:
        # Already there. A duplicate delivery, not an error: the append would collide and the case
        # is exactly where this event wants it.
        return Applied(
            case_id=case_id,
            event=event,
            stage=existing.stage,
            awaiting=AWAITING.get(existing.stage, ""),
            advanced=False,
        )

    moved = _advance(store, existing, target, payload)
    return Applied(
        case_id=case_id,
        event=event,
        stage=moved.stage,
        awaiting=AWAITING.get(moved.stage, ""),
    )


def _advance(
    store: Repository, current: Casefile, target: str, payload: dict[str, Any]
) -> Casefile:
    """One transition, with a duplicate append treated as the no-op it is."""
    evidence = payload.get("evidence") or ()
    if isinstance(evidence, str):
        evidence = (evidence,)
    try:
        return casefile.advance(
            store,
            current,
            target,
            REMEDIATION,
            note=str(payload.get("note", "")),
            evidence=tuple(str(e) for e in evidence),
            occurred_on=str(payload.get("occurred_on", "")),
        )
    except ImmutableRecord:
        # Two deliveries raced and the other one won. Re-read: the case is where it should be, and
        # this delivery has nothing left to do.
        settled = casefile.load(store, current.case_id)
        if settled is None or settled.stage != target:
            raise
        return settled


#: Re-exported so a caller can distinguish permanently-undeliverable from transient without
#: importing the control plane itself.
PERMANENT = (events.UnknownEvent, UnknownCase, UnknownStage, IllegalTransition)
