"""What has happened to this fund before, counted exactly.

The half of memory that has to be **reproducible**. A materiality assessment that comes out
differently on two runs over the same history is not an assessment, so this is a deterministic query
over recorded cases rather than a semantic search: it returns a count and the case ids behind it,
and the same history always yields the same answer.

That distinction is why this is not `BaseMemoryService`. ADK's memory interface is a semantic
`search_memory` keyed on a user id, which is the right shape for carrying narrative context between
sessions and the wrong shape for a number a regulator might ask you to justify. Narrative
carry-over uses that interface; *this* is an index.

**Why it matters at all.** Regulatory guidance on NAV errors does not treat a recurring pricing
failure the way it treats an isolated one. So the remediation office's threshold depends on how many
errors this fund has already had this quarter -- which means a fact recalled from earlier cases
changes the decision on this one. `recurrence_key` has been computed and carried on every case in
this system since early on, and until now nothing read it.

Reached by an agent as a **platform tool**, registered by the composition root. A process pack never
imports this module: `memory` is process-side in the seam scan, so neither the control plane nor
another pack may reach it, and the composition root is the only thing entitled to introduce them.
"""

from __future__ import annotations

from datetime import date
from typing import Any


#: How a NAV error case identifies its fund for recurrence purposes. Content-derived and stable, so
#: two cases for one fund collide on purpose -- that collision is the signal.
def recurrence_key_for(fund_id: str) -> str:
    return f"{fund_id}:nav_error"


def prior_errors(
    store: Any, fund_id: str, since: str, *, excluding: str = ""
) -> dict[str, object]:
    """How many NAV errors this fund has recorded on or after `since`.

    `excluding` drops the case being assessed, so a case never counts itself. Without it the first
    error of a quarter would report a prior count of one and be assessed as a repeat -- the kind of
    off-by-one that makes a governance threshold fire on the wrong side.

    **Dates are parsed, not compared as strings.** The earlier version compared ISO text on the
    argument that ordering is chronological -- true only for zero-padded dates. Measured against a
    boundary of 2026-07-01, cases stamped `2026-6-15` and `2026-5-02` both counted as *after* it,
    because `'6' > '0'`: two errors from the previous quarter selected the stricter threshold. That
    traded a loud failure mode for a silent wrong answer on a governance input, which is the wrong
    direction. A case whose `as_of` cannot be parsed is refused rather than dropped, because a
    silent undercount reads as a clean quarter.
    """
    try:
        boundary = date.fromisoformat(since)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"`since` must be an ISO date, got {since!r}") from exc

    counted = []
    unparseable = []
    for case in store.cases_by_recurrence(recurrence_key_for(fund_id)):
        if case.get("case_id") == excluding:
            continue
        try:
            when = date.fromisoformat(str(case.get("as_of", "")))
        except ValueError:
            unparseable.append(str(case.get("case_id", "?")))
            continue
        if when >= boundary:
            counted.append(case)

    if unparseable:
        raise ValueError(
            f"case(s) {sorted(unparseable)} carry an unparseable `as_of`, so the recurrence count "
            f"for {fund_id} cannot be established. Dropping them would report a quieter quarter "
            f"than the record holds."
        )
    return {
        "prior_errors": len(counted),
        "since": boundary.isoformat(),
        "fund_id": fund_id,
        # The ids, so a verdict citing the count can be checked against the cases behind it. A bare
        # number would be an unauditable claim about history.
        "case_ids": sorted(str(c.get("case_id", "")) for c in counted),
    }


def observe(result: Any, args: dict[str, Any]) -> dict[str, object]:
    """Project the count onto citable facts.

    `prior_errors` and `since` together: a count without the window it was taken over cannot be
    checked, which is the same rule the FX requirement encodes for a rate without its date.
    """
    if not isinstance(result, dict):
        return {}
    return {
        "prior_errors": result.get("prior_errors"),
        "since": result.get("since"),
        "fund_id": args.get("fund_id"),
    }
