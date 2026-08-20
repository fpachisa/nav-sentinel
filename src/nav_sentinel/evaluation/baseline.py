"""What arithmetic alone achieves, so the fleet's number has something honest beside it.

**The framing is committed here, before the numbers are seen**, because a baseline chosen after the
fact is a rhetorical device rather than a measurement.

This baseline is not a strawman. It is a deterministic rule engine reading the *same* signals the
fleet reads -- quantity, local price, FX rate and market value compared across the two books -- and
proposing the correction that reverses the difference. That is a genuinely capable classifier,
because those signals are decisive: an FX error and a pricing error differ in whether the local
price agrees, and a split differs from a settlement break in whether market value agrees exactly.

So the expected result, stated in advance:

* **Classification will be largely matched.** If it is, that is a finding and not a failure to
  report: deciding *which kind* of break this is turns out to be arithmetic, and saying otherwise
  would be claiming credit for a rule engine's work.
* **Single-leg amounts will be largely matched**, because the amount is the break itself.
* **Root-cause accuracy will be zero**, by construction. The baseline reads no published rate and no
  issuer filing, so it can state that a market value differs but not that the 14 August rate was
  applied to a 17 August valuation. It has nothing to cite.
* **Entry structure will be wrong.** It knows no contra account, so its entries do not balance; it
  cannot tell a quantity restatement from a value correction; and it cannot know that a
  trade-date-versus-settlement-date difference requires *no* entry, so it will confidently propose
  one for a break that needs none -- which in a fund's books is not a smaller error than proposing
  the wrong amount.

The defensible claims are therefore evidence citation and entry structure, neither of which a
heuristic can produce at any sample size. That is what this exists to demonstrate.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from nav_sentinel.domain.models import ExceptionCase, ReconciliationBreak

#: The account a difference of each break type lands in. As far as a rule engine can reason: the
#: break says which balance disagrees, so the correction adjusts that balance and nothing else.
_ACCOUNT_FOR = {
    "market_value": "investments_at_market",
    "cash_balance": "cash_at_bank",
    "position_quantity": "stock_record",
}


@dataclass(frozen=True)
class BaselineProposal:
    """What the rule engine concludes. Deliberately the same shape the scorer reads from the fleet,
    so the two are compared on identical terms."""

    capability: str
    root_cause: str
    legs: list[tuple[str, str | None, Decimal]]
    cited_facts: frozenset[str]


def classify(signal_lines: list[str]) -> str:
    """Rules over the signals, in the order a person would check them.

    Reading the signals rather than the books directly, so the baseline is handed exactly what the
    model is handed -- otherwise the comparison would measure the quality of the *inputs* rather
    than what each system does with them.
    """
    signals = " ".join(signal_lines)
    present = {
        "extra_line": "line the other does not" in signals,
        "quantity_agrees": "total quantity agrees" in signals,
        "value_agrees": "total market value agrees" in signals,
        "price_agrees": "local price agrees" in signals,
        "fx_agrees": "FX rate applied agrees" in signals,
        "dividend": "dividend" in signals,
        "settlement": "settlement" in signals,
    }
    rules = (
        # One book carries a line the other does not: a delivery that has not happened.
        (present["extra_line"], "nav.settlement"),
        # Shares moved and value did not: a split, arithmetically.
        (not present["quantity_agrees"] and present["value_agrees"], "nav.corporate_action"),
        # Same shares, same price, different rate: a conversion error.
        (
            present["quantity_agrees"] and present["price_agrees"] and not present["fx_agrees"],
            "nav.fx_rate",
        ),
        # Same shares, same rate, different price: the price itself is wrong.
        (
            present["quantity_agrees"] and not present["price_agrees"] and present["fx_agrees"],
            "nav.pricing",
        ),
        (present["dividend"], "nav.corporate_action"),
        (present["settlement"], "nav.settlement"),
    )
    return next((capability for holds, capability in rules if holds), "nav.unclassified")


def propose(case: ExceptionCase) -> list[tuple[str, str | None, Decimal]]:
    """Reverse the difference on the accounting side, per break.

    The one correction a rule engine can derive: the books disagree by an amount, so add its
    negation to the side that is wrong. It has no way to know which side *is* wrong, so it assumes
    accounting -- which is right five times out of six here, and is exactly why the golden includes a
    scenario where the custodian is the one in error.
    """
    legs: list[tuple[str, str | None, Decimal]] = []
    for item in case.breaks:
        account = _ACCOUNT_FOR.get(item.break_type.value)
        if account is None:
            continue
        legs.append((account, item.value_currency or _base_of(item), -item.difference))
    return legs


def _base_of(item: ReconciliationBreak) -> str | None:
    """Market values are already carried in base currency; a quantity has none at all."""
    if item.break_type.value == "position_quantity":
        return None
    return "EUR"


def run(case: ExceptionCase, signal_lines: list[str]) -> BaselineProposal:
    capability = classify(signal_lines)
    legs = propose(case)
    difference = ", ".join(f"{b.break_type.value} differs by {b.difference}" for b in case.breaks)
    return BaselineProposal(
        capability=capability,
        # The honest limit of a rule engine: it can state *that* the books disagree and by how much.
        # It cannot state why, because it has read nothing outside them.
        root_cause=f"The books disagree: {difference}.",
        legs=legs,
        # Nothing external was consulted, so nothing external can be cited.
        cited_facts=frozenset(),
    )
