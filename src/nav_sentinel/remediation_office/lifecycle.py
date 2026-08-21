"""The stages a remediation moves through, and which moves are legal.

Declared here and handed to the control plane, which owns the machine that walks it. The shape is
the process's business; validating a transition, recording it and refusing an illegal one is the
platform's.

The graph has a **branch**, and the branch is the point. An immaterial error is closed without
compensation; a material one goes the long way round through approval and payment. A linear list of
stages could not express that, which is why `Lifecycle` takes explicit edges.
"""

from __future__ import annotations

from nav_sentinel.control_plane.governance import Lifecycle

#: What a case is waiting for, at each stage. Kept beside the graph so the two cannot drift: a
#: reader asking "why has this case not moved for nine days" gets the answer from one place.
AWAITING: dict[str, str] = {
    "detected": "fund accounting to quantify the misstatement",
    "impact_assessed": "the remediation office to determine materiality",
    "materiality_determined": "an authorised approver, or closure if immaterial",
    "awaiting_approval": "a recorded four-eyes approval",
    "approved": "transfer agency to begin paying compensation",
    "compensation_in_flight": "every affected investor's payment to be confirmed",
    "closed": "nothing — the case is finished",
}

REMEDIATION = Lifecycle(
    stages=(
        #: Fund accounting has published a NAV and found it misstated.
        "detected",
        #: Transfer agency has reported who dealt at the wrong price, and for how many units.
        "impact_assessed",
        #: The remediation office has decided whether compensation and notification are required.
        "materiality_determined",
        "awaiting_approval",
        "approved",
        #: Payments are out; confirmations arrive one investor at a time, over weeks.
        "compensation_in_flight",
        "closed",
    ),
    transitions=(
        ("detected", "impact_assessed"),
        ("impact_assessed", "materiality_determined"),
        # The branch: immaterial errors are recorded and closed. There is no compensation to pay,
        # and routing one for four-eyes approval would be governance theatre.
        ("materiality_determined", "awaiting_approval"),
        ("materiality_determined", "closed"),
        ("awaiting_approval", "approved"),
        # Deliberately no ("awaiting_approval", "compensation_in_flight"). That edge is the one this
        # machine exists to refuse: a well-formed payment event arriving before anyone signed.
        ("approved", "compensation_in_flight"),
        ("compensation_in_flight", "closed"),
    ),
    initial="detected",
    terminal=("closed",),
)
