"""What can happen to a remediation case, and where each event takes it.

Pure vocabulary: a mapping from an event name to the stage it moves a case to. No control-plane
import at all, because this package may reach the platform only through `packs`, `governance` and
`gateway` -- so it declares the shape and something outside it does the walking. That is the same
division as the lifecycle itself, and the same one `transfer_agency/cycle.py` uses for the
investigator.

**The event does not choose the transition; it names a destination and the machine decides.** A
payment confirmation arriving before anyone approved names `compensation_in_flight`, the lifecycle
has no edge from `awaiting_approval` to it, and the case does not move. Letting an event carry
`from` and `to` would let a malformed or replayed message define its own legality.
"""

from __future__ import annotations

#: Event name -> the stage it moves the case to. One destination per event: an event that could
#: mean two different stages is two events.
EVENT_STAGES: dict[str, str] = {
    #: Fund accounting has quantified the misstatement and opened the case.
    "error_detected": "detected",
    #: Transfer agency has reported who dealt at the wrong price.
    "impact_reported": "impact_assessed",
    #: The remediation office has decided materiality, against this fund's recent history.
    "materiality_decided": "materiality_determined",
    #: Material: it needs signatures.
    "routed_for_approval": "awaiting_approval",
    "approval_recorded": "approved",
    "compensation_started": "compensation_in_flight",
    #: Every affected investor confirmed. Weeks after the error, and the reason this is not a
    #: long-running loop but a genuinely long-running case.
    "compensation_confirmed": "closed",
    #: Immaterial: recorded and closed with nothing paid.
    "closed_immaterial": "closed",
}

#: The one event that opens a case rather than advancing one.
OPENING_EVENT = "error_detected"


class UnknownEvent(ValueError):
    """An event name this process does not define.

    Refused rather than ignored. An unrecognised event that returned quietly would leave a case
    parked forever with nothing in the log saying why, which is the worst failure available to a
    process whose deliverable is its audit trail.
    """


def stage_for(event: str) -> str:
    """The stage an event moves a case to, or a refusal naming what is understood."""
    try:
        return EVENT_STAGES[event]
    except KeyError as exc:
        raise UnknownEvent(
            f"{event!r} is not an event this process defines. Known: {sorted(EVENT_STAGES)}"
        ) from exc
