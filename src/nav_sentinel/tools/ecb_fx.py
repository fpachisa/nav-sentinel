"""ECB euro foreign-exchange reference rates.

A genuinely authoritative public source, which is what makes the FX investigator's
conclusions checkable rather than plausible. Rates are published each TARGET business
day at 16:00 CET; there is no rate on weekends or TARGET holidays, and that absence is
itself the root cause of a large share of real-world FX breaks.
"""

from __future__ import annotations

import csv
import io
from datetime import date, timedelta
from decimal import Decimal
from functools import lru_cache

import httpx

ECB_DATA_API = "https://data-api.ecb.europa.eu/service/data/EXR"
SOURCE_NAME = "ecb_fx_reference_rates"


def _series_key(currencies: list[str]) -> str:
    return f"D.{'+'.join(sorted(currencies))}.EUR.SP00.A"


@lru_cache(maxsize=64)
def _fetch_csv(series: str, start: str, end: str) -> str:
    url = f"{ECB_DATA_API}/{series}"
    params = {"startPeriod": start, "endPeriod": end, "format": "csvdata"}
    with httpx.Client(timeout=30.0) as client:
        r = client.get(url, params=params)
        r.raise_for_status()
        return r.text


def fetch_rates(
    currencies: list[str], start: date, end: date
) -> dict[tuple[str, date], Decimal]:
    """Return {(currency, date): units of currency per 1 EUR}."""
    text = _fetch_csv(_series_key(currencies), start.isoformat(), end.isoformat())
    out: dict[tuple[str, date], Decimal] = {}
    for row in csv.DictReader(io.StringIO(text)):
        value = row.get("OBS_VALUE")
        if not value:
            continue
        ccy = row["CURRENCY"]
        day = date.fromisoformat(row["TIME_PERIOD"])
        out[(ccy, day)] = Decimal(value)
    return out


def rate_on(currency: str, day: date) -> Decimal | None:
    """Rate published for exactly this day, or None if the ECB published none."""
    if currency == "EUR":
        return Decimal(1)
    rates = fetch_rates([currency], day - timedelta(days=10), day)
    return rates.get((currency, day))


def latest_rate_on_or_before(currency: str, day: date) -> tuple[date, Decimal] | None:
    """The most recent published rate at or before `day`, with the date it belongs to.

    The gap between this date and `day` is precisely what a stale-rate break looks like.
    """
    if currency == "EUR":
        return (day, Decimal(1))
    rates = fetch_rates([currency], day - timedelta(days=14), day)
    candidates = sorted((d for (c, d) in rates if c == currency and d <= day), reverse=True)
    if not candidates:
        return None
    chosen = candidates[0]
    return (chosen, rates[(currency, chosen)])


def cross_rate(from_ccy: str, to_ccy: str, day: date) -> Decimal | None:
    """Cross via EUR, the way an accounting system should. Getting this inverted is a
    classic break, so the investigator needs the correct form to compare against."""
    if from_ccy == to_ccy:
        return Decimal(1)
    f = latest_rate_on_or_before(from_ccy, day)
    t = latest_rate_on_or_before(to_ccy, day)
    if not f or not t:
        return None
    # rate_x = units of X per EUR  =>  X per Y = (X per EUR) / (Y per EUR)
    return t[1] / f[1]
