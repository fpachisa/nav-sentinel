"""Put the demo back to its opening state, so a second take looks like a first.

Recording needs repeatability. After one run through the exception desk a case carries a verdict, a
proposal, two signatures and an approval reference, so the next take opens on a queue that is
already finished -- and re-recording is the normal case, not the exception.

**What this does not touch is the point.** Stage history, observations and policy decisions are
append-only and stay exactly where they are: they are the audit trail, and a demo tool that quietly
deleted them would falsify the one claim the demo is making. What resets is the *working state* of a
case document -- the verdict, the proposal, the signatures -- which is current-state data and
overwritable by design.

So the record of every take accumulates. That is correct, and it is also visible: the audit view will
show more decisions than the current take produced, which is a true statement about a system that
has been run several times.
"""

from __future__ import annotations

import sys
from datetime import date

from nav_sentinel import composition
from nav_sentinel.webapp import workflow

#: The working fields an analyst produces. Everything else on the document is detection output.
WORKING = ("verdict", "proposal", "triage", "routed", "refusal", "investigator",
           "signed_by", "signed_roles", "approval_ref", "last_outcome")


def reset(as_of: date = workflow.DEFAULT_AS_OF) -> int:
    store = composition.store()
    cleared = 0
    for item in workflow.queue(as_of):
        document = store.load_case(item.case_id)
        if not document:
            continue
        touched = {field for field in WORKING if field in document}
        if not touched:
            continue
        for field in touched:
            document.pop(field)
        store.save_case(item.case_id, document)
        cleared += 1
    return cleared


def main() -> int:
    composition.configure()
    store = composition.store()
    cleared = reset()
    kept = sum(len(store.decisions_for(i.case_id)) for i in workflow.queue())
    print(f"reset {cleared} case(s) to their opening state")
    print(f"kept {kept} recorded policy decisions — the audit trail is append-only and untouched")
    return 0


if __name__ == "__main__":
    sys.exit(main())
