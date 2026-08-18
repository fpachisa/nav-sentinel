"""Generate the synthetic books and records, with deliberately seeded breaks.

Design notes
------------
Two funds are built twice over: once as the *accounting* book and once as the *custodian*
book. Each seeded scenario distorts exactly one side by a known amount, and that amount is
written to ``eval/golden_breaks.yaml``.

That file is what makes this project measurable rather than merely demonstrable: a break
either reconciles to zero against the recorded correction or it does not, so the fleet can
be scored on root-cause accuracy without a model grading another model.

Real ECB reference rates are used throughout, so the FX investigator resolves against the
same authoritative public source a fund accountant would.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import yaml

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nav_sentinel.tools import ecb_fx  # noqa: E402

DATA = Path(__file__).parent / "data"
EVAL = Path(__file__).resolve().parents[1] / "eval"

NAV_DATE = date(2026, 8, 17)      # Monday; ECB published a rate on this day
PRIOR_DATE = date(2026, 8, 14)    # Friday; the stale rate an accounting system might reuse

CENTS = Decimal("0.01")
UNITS = Decimal("0.0001")


def money(d: Decimal) -> Decimal:
    return d.quantize(CENTS, rounding=ROUND_HALF_UP)


def qty(d: Decimal) -> Decimal:
    return d.quantize(UNITS, rounding=ROUND_HALF_UP)


# --------------------------------------------------------------------------- static data

SECURITIES = [
    # isin, ticker, name, ccy, country, type, is_dr, dr_ratio
    ("US0378331005", "AAPL", "Apple Inc.", "USD", "US", "equity", False, None),
    ("US5949181045", "MSFT", "Microsoft Corp.", "USD", "US", "equity", False, None),
    ("NL0011821202", "INGA", "ING Groep N.V.", "EUR", "NL", "equity", False, None),
    ("FR0000121014", "MC", "LVMH SE", "EUR", "FR", "equity", False, None),
    ("GB0009252882", "GSK", "GSK plc", "GBP", "GB", "equity", False, None),
    ("US0028241000", "ABEV", "Ambev S.A. ADR", "USD", "US", "equity", True, "1:1"),
    ("US4581401001", "INTC", "Intel Corp.", "USD", "US", "equity", False, None),
    ("DE0007236101", "SIE", "Siemens AG", "EUR", "DE", "equity", False, None),
    ("JP3633400001", "7203", "Toyota Motor Corp.", "JPY", "JP", "equity", False, None),
    ("US7170811035", "PFE", "Pfizer Inc.", "USD", "US", "equity", False, None),
]

FUNDS = [
    {
        "fund_id": "MERID-GEF",
        "name": "Meridian Global Equity Fund",
        "base_currency": "EUR",
        "domicile": "IE",
        "shares_outstanding": "4250000",
        "fee_bps_annual": "75",
    },
    {
        "fund_id": "ATLAS-USE",
        "name": "Atlas US Core Equity Fund",
        "base_currency": "USD",
        "domicile": "LU",
        "shares_outstanding": "9800000",
        "fee_bps_annual": "60",
    },
]

# fund_id, isin, quantity, local_price
HOLDINGS = [
    ("MERID-GEF", "US0378331005", "185000", "241.50"),
    ("MERID-GEF", "NL0011821202", "620000", "18.94"),
    ("MERID-GEF", "FR0000121014", "42000", "612.80"),
    ("MERID-GEF", "US0028241000", "1450000", "2.86"),   # ADR -> dividend scenario
    ("MERID-GEF", "DE0007236101", "88000", "227.35"),
    ("MERID-GEF", "US5949181045", "96000", "512.40"),   # split scenario
    ("MERID-GEF", "JP3633400001", "310000", "3184.00"),
    ("ATLAS-USE", "US0378331005", "540000", "241.50"),
    ("ATLAS-USE", "US5949181045", "295000", "512.40"),
    ("ATLAS-USE", "GB0009252882", "1120000", "16.42"),  # inverted cross scenario
    ("ATLAS-USE", "US4581401001", "2350000", "27.18"),  # stale price scenario
    ("ATLAS-USE", "US7170811035", "1880000", "31.04"),
]

SEC_BY_ISIN = {s[0]: s for s in SECURITIES}
FUND_BY_ID = {f["fund_id"]: f for f in FUNDS}


def local_per_base(local_ccy: str, base_ccy: str, day: date) -> Decimal:
    """Units of `local_ccy` per one unit of `base_ccy`, via the ECB's EUR cross."""
    if local_ccy == base_ccy:
        return Decimal("1")
    l = ecb_fx.latest_rate_on_or_before(local_ccy, day)
    b = ecb_fx.latest_rate_on_or_before(base_ccy, day)
    if l is None or b is None:
        raise RuntimeError(f"no ECB rate for {local_ccy}/{base_ccy} on {day}")
    return l[1] / b[1]


def make_position(fund_id, isin, quantity, price, source, day, *, fx_day=None, price_override=None,
                  quantity_override=None, fx_override=None):
    fund = FUND_BY_ID[fund_id]
    sec = SEC_BY_ISIN[isin]
    local_ccy, base_ccy = sec[3], fund["base_currency"]
    rate = fx_override if fx_override is not None else local_per_base(local_ccy, base_ccy, fx_day or day)
    q = qty(Decimal(quantity_override if quantity_override is not None else quantity))
    p = Decimal(price_override if price_override is not None else price)
    mv = money(q * p / rate)
    return {
        "fund_id": fund_id,
        "isin": isin,
        "as_of": day.isoformat(),
        "quantity": str(q),
        "local_price": str(p),
        "local_currency": local_ccy,
        "fx_rate": str(rate.quantize(Decimal("0.00000001"))),
        "market_value_base": str(mv),
        "source": source,
    }


def build() -> dict:
    golden: list[dict] = []
    acc_pos, cus_pos = [], []

    # ---- positions -------------------------------------------------------------
    for fund_id, isin, quantity, price in HOLDINGS:
        acc_kwargs: dict = {}
        cus_kwargs: dict = {}
        note = None

        # Scenario 1 -- stale FX rate on the accounting side (Friday's rate on Monday).
        if (fund_id, isin) == ("MERID-GEF", "US0378331005"):
            acc_kwargs["fx_day"] = PRIOR_DATE
            correct = local_per_base("USD", "EUR", NAV_DATE)
            stale = local_per_base("USD", "EUR", PRIOR_DATE)
            q, p = Decimal(quantity), Decimal(price)
            delta = money(q * p / stale) - money(q * p / correct)
            note = {
                "scenario": "FX_STALE_RATE",
                "fund_id": fund_id,
                "isin": isin,
                "expected_category": "fx_rate",
                "incorrect_side": "accounting",
                "root_cause": (
                    f"Accounting valued the USD position using the ECB reference rate for "
                    f"{PRIOR_DATE} ({stale}) instead of {NAV_DATE} ({correct})."
                ),
                "expected_correction_base": str(-delta),
                "verifiable_against": "ecb_fx_reference_rates",
            }

        # Scenario 2 -- 2:1 share split applied by the custodian, missed by accounting.
        elif (fund_id, isin) == ("MERID-GEF", "US5949181045"):
            cus_kwargs["quantity_override"] = str(Decimal(quantity) * 2)
            cus_kwargs["price_override"] = str(Decimal(price) / 2)
            note = {
                "scenario": "CA_STOCK_SPLIT_NOT_APPLIED",
                "fund_id": fund_id,
                "isin": isin,
                "expected_category": "corporate_action",
                "incorrect_side": "accounting",
                "root_cause": (
                    "A 2:1 share split effective 2026-08-17 was applied by the custodian but "
                    "not by the accounting book. Quantity differs 2x; market value agrees."
                ),
                "expected_correction_base": "0.00",
                "expected_quantity_correction": str(Decimal(quantity)),
                "verifiable_against": "sec_edgar",
            }

        # Scenario 3 -- inverted FX cross on a GBP holding in a USD-base fund.
        elif (fund_id, isin) == ("ATLAS-USE", "GB0009252882"):
            correct = local_per_base("GBP", "USD", NAV_DATE)
            acc_kwargs["fx_override"] = Decimal("1") / correct
            q, p = Decimal(quantity), Decimal(price)
            delta = money(q * p / (Decimal("1") / correct)) - money(q * p / correct)
            note = {
                "scenario": "FX_INVERTED_CROSS",
                "fund_id": fund_id,
                "isin": isin,
                "expected_category": "fx_rate",
                "incorrect_side": "accounting",
                "root_cause": (
                    f"Accounting applied the GBP/USD cross inverted: used "
                    f"{(Decimal('1')/correct).quantize(Decimal('0.000001'))} where the correct "
                    f"GBP-per-USD rate on {NAV_DATE} is {correct.quantize(Decimal('0.000001'))}."
                ),
                "expected_correction_base": str(-delta),
                "verifiable_against": "ecb_fx_reference_rates",
            }

        # Scenario 4 -- stale price on an illiquid line, accounting side.
        elif (fund_id, isin) == ("ATLAS-USE", "US4581401001"):
            stale_price = Decimal("26.71")
            acc_kwargs["price_override"] = str(stale_price)
            q = Decimal(quantity)
            delta = money(q * stale_price) - money(q * Decimal(price))
            note = {
                "scenario": "PRICE_STALE",
                "fund_id": fund_id,
                "isin": isin,
                "expected_category": "pricing",
                "incorrect_side": "accounting",
                "root_cause": (
                    f"Accounting carried the prior close of {stale_price} rather than the "
                    f"{NAV_DATE} close of {price}; the vendor price feed did not refresh."
                ),
                "expected_correction_base": str(-delta),
                "verifiable_against": "books_and_records",
            }

        acc_pos.append(make_position(fund_id, isin, quantity, price, "accounting", NAV_DATE, **acc_kwargs))
        cus_pos.append(make_position(fund_id, isin, quantity, price, "custodian", NAV_DATE, **cus_kwargs))
        if note:
            golden.append(note)

    # ---- unsettled trade recognised on trade date by accounting only -----------
    trades = [
        {
            "trade_id": "TRD-2026-08-17-0041",
            "fund_id": "MERID-GEF",
            "isin": "FR0000121014",
            "trade_date": NAV_DATE.isoformat(),
            "settlement_date": date(2026, 8, 19).isoformat(),
            "side": "BUY",
            "quantity": "8500",
            "price": "612.80",
            "currency": "EUR",
            "status": "pending",
        },
        {
            "trade_id": "TRD-2026-08-14-0118",
            "fund_id": "ATLAS-USE",
            "isin": "US7170811035",
            "trade_date": PRIOR_DATE.isoformat(),
            "settlement_date": NAV_DATE.isoformat(),
            "side": "BUY",
            "quantity": "125000",
            "price": "31.04",
            "currency": "USD",
            "status": "failed",
        },
    ]

    # Scenario 5 -- accounting includes a pending T+2 purchase the custodian has not settled.
    pending = trades[0]
    acc_pos.append(
        make_position(pending["fund_id"], pending["isin"], pending["quantity"],
                      pending["price"], "accounting", NAV_DATE)
    )
    golden.append({
        "scenario": "SETTLE_TRADE_DATE_VS_SETTLEMENT_DATE",
        "fund_id": pending["fund_id"],
        "isin": pending["isin"],
        "expected_category": "settlement",
        "incorrect_side": "neither",
        "root_cause": (
            f"Trade {pending['trade_id']} was executed {NAV_DATE} for settlement 2026-08-19. "
            "Accounting recognises on trade date, the custodian on settlement date. This is a "
            "timing difference, not an error: it requires an unsettled-trade reconciling item, "
            "not a correcting entry."
        ),
        "expected_correction_base": "0.00",
        "verifiable_against": "books_and_records",
    })

    # Scenario 6 -- a failed trade still carried as settled in the accounting book.
    failed = trades[1]
    acc_pos.append(
        make_position(failed["fund_id"], failed["isin"], failed["quantity"],
                      failed["price"], "accounting", NAV_DATE)
    )
    golden.append({
        "scenario": "SETTLE_FAILED_TRADE",
        "fund_id": failed["fund_id"],
        "isin": failed["isin"],
        "expected_category": "settlement",
        "incorrect_side": "accounting",
        "root_cause": (
            f"Trade {failed['trade_id']} failed at the custodian on {NAV_DATE} but remains "
            "in the accounting book as settled. The position must be reversed."
        ),
        "expected_correction_base": str(-money(Decimal(failed["quantity"]) * Decimal(failed["price"]))),
        "verifiable_against": "books_and_records",
    })

    # ---- cash ------------------------------------------------------------------
    acc_cash, cus_cash = [], []

    def cash(mid, fund_id, ccy, amount, mtype, desc, source, day=NAV_DATE):
        return {
            "movement_id": mid, "fund_id": fund_id, "value_date": day.isoformat(),
            "currency": ccy, "amount": str(money(Decimal(amount))), "movement_type": mtype,
            "description": desc, "source": source,
        }

    for src, bucket in (("accounting", acc_cash), ("custodian", cus_cash)):
        bucket.append(cash("CSH-M-001", "MERID-GEF", "EUR", "12450000.00", "subscription",
                           "Opening cash", src))
        bucket.append(cash("CSH-A-001", "ATLAS-USE", "USD", "28900000.00", "subscription",
                           "Opening cash", src))

    # Scenario 7 -- ADR dividend booked gross by accounting, net of 15% withholding by custodian.
    gross = Decimal("1450000") * Decimal("0.1750")     # 253,750.00 USD
    withholding = money(gross * Decimal("0.15"))
    net = money(gross - withholding)
    acc_cash.append(cash("CSH-M-DIV-ABEV", "MERID-GEF", "USD", str(money(gross)), "dividend",
                         "ABEV ADR dividend, gross", "accounting"))
    cus_cash.append(cash("CSH-M-DIV-ABEV", "MERID-GEF", "USD", str(net), "dividend",
                         "ABEV ADR dividend, net of withholding tax", "custodian"))
    golden.append({
        "scenario": "CA_ADR_DIVIDEND_GROSS_VS_NET",
        "fund_id": "MERID-GEF",
        "isin": "US0028241000",
        "expected_category": "corporate_action",
        "incorrect_side": "accounting",
        "root_cause": (
            f"The ABEV ADR dividend was booked gross ({money(gross)} USD) in the accounting "
            f"book while the custodian credited net of 15% withholding tax ({net} USD). "
            f"A withholding tax receivable of {withholding} USD is missing."
        ),
        "expected_correction_base": str(-withholding),
        "correction_currency": "USD",
        "verifiable_against": "sec_edgar",
    })

    # Scenario 8 -- one day of management fee accrual missing from the accounting book.
    daily_fee = money(Decimal("480000000") * Decimal("75") / Decimal("10000") / Decimal("365"))
    cus_cash.append(cash("CSH-M-FEE-0817", "MERID-GEF", "EUR", str(-daily_fee), "fee",
                         "Management fee accrual 2026-08-17", "custodian"))
    golden.append({
        "scenario": "FEE_ACCRUAL_MISSING",
        "fund_id": "MERID-GEF",
        "expected_category": "cash_fees",
        "incorrect_side": "accounting",
        "root_cause": (
            f"One day of management fee accrual ({daily_fee} EUR at 75bps annual on ~480m "
            "net assets) is absent from the accounting book for 2026-08-17."
        ),
        "expected_correction_base": str(-daily_fee),
        "correction_currency": "EUR",
        "verifiable_against": "books_and_records",
    })

    # Scenario 9 -- deposit interest credited by the custodian, not yet accrued by accounting.
    interest = money(Decimal("28900000") * Decimal("0.0325") / Decimal("365") * Decimal("3"))
    cus_cash.append(cash("CSH-A-INT-0817", "ATLAS-USE", "USD", str(interest), "interest",
                         "Deposit interest 2026-08-14 to 2026-08-17", "custodian"))
    golden.append({
        "scenario": "INTEREST_ACCRUAL_MISSING",
        "fund_id": "ATLAS-USE",
        "expected_category": "cash_fees",
        "incorrect_side": "accounting",
        "root_cause": (
            f"Three days of deposit interest ({interest} USD at 3.25% on 28.9m) was credited "
            "by the custodian but not accrued in the accounting book."
        ),
        "expected_correction_base": str(interest),
        "correction_currency": "USD",
        "verifiable_against": "books_and_records",
    })

    # ---- NAV, derived from each side's own book so the break falls out naturally ----
    def nav_for(fund_id: str, positions: list[dict], cashes: list[dict], source: str) -> dict:
        fund = FUND_BY_ID[fund_id]
        base = fund["base_currency"]
        assets = sum(Decimal(p["market_value_base"]) for p in positions if p["fund_id"] == fund_id)
        for c in cashes:
            if c["fund_id"] != fund_id:
                continue
            amt = Decimal(c["amount"])
            if c["currency"] != base:
                amt = amt / local_per_base(c["currency"], base, NAV_DATE)
            assets += amt
        return {
            "fund_id": fund_id,
            "as_of": NAV_DATE.isoformat(),
            "total_assets_base": str(money(assets)),
            "total_liabilities_base": "0.00",
            "shares_outstanding": fund["shares_outstanding"],
            "source": source,
        }

    acc_nav = [nav_for(f["fund_id"], acc_pos, acc_cash, "accounting") for f in FUNDS]
    cus_nav = [nav_for(f["fund_id"], cus_pos, cus_cash, "custodian") for f in FUNDS]

    return {
        "securities": [
            {"isin": s[0], "ticker": s[1], "name": s[2], "currency": s[3], "country": s[4],
             "security_type": s[5], "is_depositary_receipt": s[6], "dr_ratio": s[7]}
            for s in SECURITIES
        ],
        "funds": FUNDS,
        "positions_accounting": acc_pos,
        "positions_custodian": cus_pos,
        "cash_accounting": acc_cash,
        "cash_custodian": cus_cash,
        "nav_accounting": acc_nav,
        "nav_custodian": cus_nav,
        "trades": trades,
        "_golden": golden,
    }


def main() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    EVAL.mkdir(parents=True, exist_ok=True)
    book = build()
    golden = book.pop("_golden")

    for name, payload in book.items():
        (DATA / f"{name}.json").write_text(json.dumps(payload, indent=2) + "\n")

    (EVAL / "golden_breaks.yaml").write_text(
        yaml.safe_dump(
            {"nav_date": NAV_DATE.isoformat(), "scenarios": golden},
            sort_keys=False, width=100, allow_unicode=True,
        )
    )

    print(f"wrote {len(book)} fixture files to {DATA}")
    print(f"wrote {len(golden)} golden scenarios to {EVAL / 'golden_breaks.yaml'}")
    for g in golden:
        print(f"  - {g['scenario']:42s} {g['expected_category']}")


if __name__ == "__main__":
    main()
