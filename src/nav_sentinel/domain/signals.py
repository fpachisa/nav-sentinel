"""Deterministic signals that narrow a break to a category, computed before any model sees it.

Triage was given two numbers -- what each book says the position is worth -- and asked which kind of
problem that is. Measured, it scored 2 of 6 with two *confident* wrong answers, and it could not
have done better: a market value that disagrees while quantity agrees is an FX error or a pricing
error, and those two are indistinguishable from the totals alone.

They are not indistinguishable from the books. Same quantity, same local price, different FX rate is
an FX error. Quantity differing by a whole ratio while market value agrees exactly is a split. Those
are facts, and computing a fact with a model is the wrong tool -- so this module extracts them and
the model's job narrows to naming the category the evidence points at.

This is the same division the deterministic spine already uses: arithmetic where arithmetic works,
a model only where judgement is genuinely required.

**The reads go through the gateway**, under whichever identity is bound. They did not, at first, and
that was a P-006 bypass of exactly the kind this project exists to prevent: these facts end up in a
model's prompt, so an agent whose manifest declares no position scope was being handed position data
with no policy decision recorded anywhere. Computing something on an agent's behalf is still reading
it on the agent's behalf. Triage's manifest now declares the two tools and the two scopes, and the
governance log shows the reads.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from nav_sentinel.control_plane import gateway

if TYPE_CHECKING:  # pragma: no cover
    from nav_sentinel.domain.models import ExceptionCase, ReconciliationBreak

#: Ratios worth naming. A quantity difference of exactly 2x or 3x is a split or a reverse split;
#: an arbitrary ratio is not, and calling it one would be the confident wrong answer this exists to
#: prevent.
WHOLE_RATIOS = (Decimal(2), Decimal(3), Decimal(4), Decimal(5), Decimal("0.5"), Decimal("0.25"))


def _lots(source: str, isin: str, as_of, fund_id: str | None = None) -> list:
    """Every position row for this security on this date, not the first one.

    A book can hold more than one lot -- and a book holding a lot the other does not *is* the
    settlement signal. Taking `next(...)` reported "quantity agrees (400,000)" for a break that
    said 520,000 against 400,000, because accounting held 400,000 plus a pending 120,000 lot and
    the first row happened to match. A signal that contradicts the break it describes is worse than
    no signal: triage was handed facts that denied the problem existed, and correctly refused to
    classify anything.
    """
    rows = gateway.call_tool("books_and_records.positions", source, fund_id)
    return [p for p in rows if p.isin == isin and p.as_of == as_of]


def for_break(item: ReconciliationBreak) -> list[str]:
    """What the books say about this break, beyond the two totals that disagree."""
    if item.isin:
        return _position_signals(item)
    return _cash_signals(item)


def _position_signals(item: ReconciliationBreak) -> list[str]:
    books = {
        source: _lots(source, item.isin, item.as_of, item.fund_id)
        for source in ("accounting", "custodian")
    }
    if not any(books.values()):
        return ["neither book holds this security on this date"]
    for source, lots in books.items():
        if not lots:
            other = "custodian" if source == "accounting" else "accounting"
            return [f"{other} holds this security and {source} has no position at all"]

    signals: list[str] = []
    totals: dict[str, dict[str, Decimal]] = {}
    for source, lots in books.items():
        totals[source] = {
            "quantity": sum((lot.quantity for lot in lots), Decimal(0)),
            "market_value": sum((lot.market_value_base for lot in lots), Decimal(0)),
        }

    # Lot counts first: a book carrying a line the other does not is the settlement signal, and it
    # is invisible in the totals once they are summed.
    counts = {source: len(lots) for source, lots in books.items()}
    if counts["accounting"] != counts["custodian"]:
        signals.append(
            f"accounting holds {counts['accounting']} position line(s) and custodian holds "
            f"{counts['custodian']} -- one book has a line the other does not"
        )

    for label, key in (("quantity", "quantity"), ("market value", "market_value")):
        mine, theirs = totals["accounting"][key], totals["custodian"][key]
        if mine == theirs:
            signals.append(f"total {label} agrees ({mine})")
        else:
            signals.append(f"total {label} differs: accounting {mine}, custodian {theirs}")

    for label, attribute in (("local price", "local_price"), ("FX rate applied", "fx_rate")):
        signals.append(_per_lot_signal(books, label, attribute))

    accounting_quantity = totals["accounting"]["quantity"]
    if accounting_quantity:
        ratio = totals["custodian"]["quantity"] / accounting_quantity
        if ratio in WHOLE_RATIOS:
            signals.append(
                f"the quantity difference is exactly {ratio}x -- a whole ratio, not a rounding "
                f"or partial-fill difference"
            )
    return signals


def _cash_signals(item: ReconciliationBreak) -> list[str]:
    """For a cash break, the movements that make up each book's balance as at the date.

    A cash balance difference carries no security identifier, so the informative fact is *what kind
    of cash entry* the two books disagree about -- a dividend, a fee accrual, a settlement.

    **As at, not on.** `detect_cash_breaks` strikes the break as a balance of every movement up to
    and including the valuation date, so filtering the signals to that one date described a
    different quantity from the one that disagreed. Measured on the USD cash case: the signals
    showed two dividends implying a difference of 38,062.50 -- 1.03% of a 3,686,737.50 break -- and
    omitted the 3,724,800.00 failed settlement that is 99% of it. The model then confidently routed
    it to corporate actions, whose manifest holds no trades tool and could never have resolved it.

    Each movement's date is shown because two dividends across two cycles are otherwise
    indistinguishable, and the totals are stated so the signal reconciles to the break by
    construction rather than by the reader's arithmetic.
    """
    signals: list[str] = []
    balances: dict[str, Decimal] = {}
    for source in ("accounting", "custodian"):
        movements = [
            m
            for m in gateway.call_tool(
                "books_and_records.cash_movements", source, item.fund_id
            )
            if m.value_date <= item.as_of
            and (item.currency is None or m.currency == item.currency)
        ]
        balances[source] = sum((m.amount for m in movements), Decimal(0))
        if not movements:
            signals.append(f"{source} has no cash movements at all as at this date")
            continue
        described = "; ".join(
            f"{m.value_date.isoformat()} {m.movement_type} {m.amount}"
            for m in sorted(movements, key=lambda m: (m.value_date, m.movement_id))
        )
        signals.append(f"{source} cash movements as at this date: {described}")

    signals.append(
        f"balances as at this date: accounting {balances['accounting']}, "
        f"custodian {balances['custodian']} "
        f"(difference {balances['accounting'] - balances['custodian']})"
    )
    return signals


def _per_lot_signal(books: dict[str, list], label: str, attribute: str) -> str:
    """Compare a per-lot attribute across the two books.

    Prices and rates belong to a lot, not to a holding, so they are compared as sets. Two books
    quoting one price each is the common case and reads plainly; anything else is stated as what it
    is rather than flattened into a single number that belongs to neither.

    Rendered as plain text, because these lines go into a prompt and `[Decimal('512.40')]` is noise
    a model has to see past.
    """
    quoted = {
        source: [str(v) for v in sorted({getattr(lot, attribute) for lot in lots})]
        for source, lots in books.items()
    }
    if quoted["accounting"] != quoted["custodian"]:
        return (
            f"{label} differs: accounting {', '.join(quoted['accounting'])}, "
            f"custodian {', '.join(quoted['custodian'])}"
        )
    agreed = quoted["accounting"]
    if len(agreed) == 1:
        return f"{label} agrees ({agreed[0]})"
    return f"{label} matches across both books ({', '.join(agreed)})"


def for_case(case: ExceptionCase) -> list[str]:
    """Signals for every break in a case, de-duplicated and in a stable order.

    Stable because the prompt is part of a reproducible run: two identical cycles must produce
    identical instructions, or `make eval` cannot be compared against itself.
    """
    seen: dict[str, None] = {}
    for item in case.breaks:
        for signal in for_break(item):
            seen.setdefault(signal, None)
    return list(seen)
