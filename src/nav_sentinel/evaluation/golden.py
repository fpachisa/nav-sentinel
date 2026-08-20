"""The golden file, typed.

Loaded through a model rather than read as nested dicts because the eval's numbers depend on it: a
scenario missing `expected_corrections` and a scenario with an empty list mean different things --
the first is a break with nothing to correct, the second is a data error -- and dict access cannot
tell them apart.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Iterator

GOLDEN = Path(__file__).resolve().parents[3] / "eval" / "golden_breaks.yaml"

#: How close a correction must be to count. One cent, because the fixtures reconcile to the cent
#: and not beyond: converting a USD leg into EUR base leaves sub-cent rounding, so demanding exact
#: equality would fail a correct answer. Measured residuals on the golden itself: 0.0059 and 0.0074.
TOLERANCE = Decimal("0.01")


class Correction(BaseModel):
    """One expected leg of a correction, signed as the amount to add to the accounting book."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    leg: str
    account: str
    currency: str | None = None
    amount: Decimal
    quantity: Decimal | None = None

    @property
    def is_quantity(self) -> bool:
        """A restatement: the golden states `amount: 0.00` and a `quantity`, because a split moves
        no net assets."""
        return self.quantity is not None

    def matches(self, account: str, currency: str | None, amount: Decimal) -> bool:
        """Same account, same currency, same amount to the cent."""
        return (
            account == self.account
            and currency == self.currency
            and abs(amount - self.amount) <= TOLERANCE
        )

    def matches_quantity(self, account: str, delta: Decimal) -> bool:
        """Same account, same share delta -- compared against `quantity`, not `amount`.

        A restatement's amount is zero by definition, so matching it on money scored a correct split
        as a miss.
        """
        return (
            self.is_quantity
            and account == self.account
            and abs(delta - (self.quantity or Decimal(0))) <= TOLERANCE
        )


class Scenario(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    scenario: str
    capability: str
    isin: str | None = None
    #: Which book is wrong. One scenario has the *custodian* in error, which is the adversarial case
    #: that stops "always correct the accounting side" from scoring as understanding.
    incorrect_side: str = "accounting"
    root_cause: str
    expected_corrections: list[Correction] = Field(default_factory=list)
    verifiable_against: str | None = None
    evidence_must_cite: list[str] = Field(default_factory=list)
    #: Whether this break appears in an earlier cycle too. What S2's recurrence claim is measured
    #: against.
    recurs: bool = False
    #: Stated explicitly rather than inferred from an empty correction list. "This break needs no
    #: entry" and "nobody wrote the entry down" are different facts, and only the golden knows which.
    reconciling_item: bool | None = None
    #: The discriminator between two scenarios of the same capability -- the two settlement cases
    #: differ only in whether the settlement date has passed. Recorded so the eval can say *why* a
    #: classifier confused them rather than only that it did.
    distinguished_by: str | None = None

    @property
    def key(self) -> str:
        """How a scenario is matched to a case: by security, or by the cash it concerns."""
        return self.isin or f"cash:{self._cash_currency()}"

    def _cash_currency(self) -> str:
        currencies = {c.currency for c in self.expected_corrections if c.currency}
        return next(iter(sorted(currencies)), "")

    @property
    def posts_nothing(self) -> bool:
        """A reconciling item: both books are right and the difference is timing.

        Taken from the golden's own flag where it states one, because an empty correction list is
        ambiguous -- it could mean "nothing to post" or "nobody filled this in".
        """
        if self.reconciling_item is not None:
            return self.reconciling_item
        return not self.expected_corrections


class Cycle(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    nav_date: date
    control_total: Decimal
    scenarios: list[Scenario]


class Golden(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    fund_id: str
    base_currency: str
    cycles: list[Cycle]
    notes: str = ""

    def cycle(self, as_of: date) -> Cycle:
        for cycle in self.cycles:
            if cycle.nav_date == as_of:
                return cycle
        raise KeyError(f"no golden cycle for {as_of.isoformat()}")

    def scenarios(self) -> Iterator[tuple[date, Scenario]]:
        for cycle in self.cycles:
            for scenario in cycle.scenarios:
                yield cycle.nav_date, scenario


def load(path: Path = GOLDEN) -> Golden:
    return Golden.model_validate(yaml.safe_load(path.read_text()))
