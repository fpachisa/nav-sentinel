"""Whether a published NAV error is material, and which threshold decided it.

Arithmetic, and that is the claim rather than a shortcut. Comparing an error in basis points against
a threshold is subtraction; what needs judgement is establishing the *inputs* -- how big the error
was, how many investors dealt at the wrong price, and how many times this has happened before -- and
each of those is evidence an agent must cite. Same division as the transfer-agency correction: the
model establishes and attributes, the arithmetic computes, and neither pretends to be the other.

**The threshold depends on history, and that is the point of remembering anything.** Regulatory
guidance does not treat a recurring pricing failure the way it treats an isolated one: a control
that keeps failing is a different finding from a control that failed once, even when the numbers are
identical. So a fund with prior errors in the window is assessed against a *lower* bar, and an error
that would have been immaterial on its own becomes material as a repeat.

That is a fact recalled from earlier cases changing the decision on this one. Without it, "memory"
would mean a database read.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

#: An isolated error. Below this, the misstatement is recorded and the case closed with nothing paid.
#: Chosen to sit under the 50bps figure common in industry guidance rather than to flatter a demo.
ISOLATED_THRESHOLD_BPS = Decimal(50)

#: A repeat. A lower bar, because the finding is the recurrence and not only the size.
RECURRING_THRESHOLD_BPS = Decimal(20)

#: Prior errors in the window at which the stricter threshold applies. Two, so the *third* error is
#: the one assessed differently -- one prior event is not yet a pattern.
RECURRENCE_TRIGGER = 2


@dataclass(frozen=True)
class Assessment:
    """A materiality decision, with everything needed to check it."""

    material: bool
    error_bps: Decimal
    threshold_bps: Decimal
    #: "isolated" or "recurring". Named, because *which* threshold applied is the reviewable part.
    basis: str
    prior_errors: int
    since: str
    affected_investors: int

    @property
    def requires_compensation(self) -> bool:
        """Material and somebody actually dealt. An error nobody transacted on harms nobody.

        Kept distinct from `material` because they are different findings: an error can be material
        for reporting and recurrence purposes while there is no investor to compensate, and
        collapsing them would either pay nobody or overstate the remediation.
        """
        return self.material and self.affected_investors > 0

    @property
    def rationale(self) -> str:
        verdict = "material" if self.material else "not material"
        pattern = (
            f"the {self.prior_errors} prior error(s) since {self.since} put this fund over the "
            f"recurrence trigger of {RECURRENCE_TRIGGER}, so the stricter threshold applies"
            if self.basis == "recurring"
            else f"{self.prior_errors} prior error(s) since {self.since} is below the recurrence "
                 f"trigger of {RECURRENCE_TRIGGER}, so the isolated threshold applies"
        )
        consequence = (
            f"{self.affected_investors} investors dealt at the misstated price and require "
            f"compensation"
            if self.requires_compensation
            else "no investor dealt at the misstated price, so there is nothing to compensate"
            if self.material
            else "the error is recorded and the case closes with nothing paid"
        )
        return (
            f"{self.error_bps}bps against a {self.threshold_bps}bps threshold: {verdict}. "
            f"{pattern.capitalize()}. {consequence.capitalize()}."
        )


def assess(
    *, error_bps: Decimal, affected_investors: int, prior_errors: int, since: str
) -> Assessment:
    """Decide materiality from the error, the population and the fund's recent history.

    `prior_errors` excludes the case being assessed. Counting the case itself would make the first
    error of a quarter report one prior event, which is the kind of off-by-one that fires a
    governance threshold on the wrong side -- so the exclusion is the caller's contract and
    `memory.prior_errors` takes an `excluding` argument for it.
    """
    if error_bps < 0:
        raise ValueError(
            f"error_bps is {error_bps}; materiality is assessed on magnitude, and a signed value "
            f"here would make an overstatement of 285bps compare as smaller than an "
            f"understatement of 20bps"
        )
    if affected_investors < 0:
        raise ValueError(f"affected_investors is {affected_investors}")
    if prior_errors < 0:
        raise ValueError(f"prior_errors is {prior_errors}")

    recurring = prior_errors >= RECURRENCE_TRIGGER
    threshold = RECURRING_THRESHOLD_BPS if recurring else ISOLATED_THRESHOLD_BPS
    return Assessment(
        material=error_bps >= threshold,
        error_bps=error_bps,
        threshold_bps=threshold,
        basis="recurring" if recurring else "isolated",
        prior_errors=prior_errors,
        since=since,
        affected_investors=affected_investors,
    )
