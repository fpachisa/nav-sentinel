"""Base-currency conversion backed by the ECB, cached per cycle.

Injected into materiality and cycle arithmetic so that scoring stays a pure function of
its inputs and can be tested without network access.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from nav_sentinel.tools import ecb_fx


def make_to_base(base_currency: str, as_of: date):
    cache: dict[str, Decimal] = {}

    def to_base(amount: Decimal, currency: str) -> Decimal:
        if currency == base_currency:
            return amount
        if currency not in cache:
            local = ecb_fx.latest_rate_on_or_before(currency, as_of)
            base = ecb_fx.latest_rate_on_or_before(base_currency, as_of)
            if local is None or base is None:
                raise RuntimeError(f"no ECB rate to convert {currency}->{base_currency} on {as_of}")
            cache[currency] = local[1] / base[1]  # units of `currency` per unit of base
        return amount / cache[currency]

    return to_base
