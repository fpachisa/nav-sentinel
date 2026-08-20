"""Generate the synthetic books and records, and the ground truth they are scored against.

Design notes, because two of these are the direct result of defects found in review.

**Double entry is not optional.** The previous generator recognised a trade-date purchase by
adding the position and nothing else, so net assets moved by the full value of the trade. The
declared ground truth then explained 2.4% of the fund's NAV difference. Every recognition here
books both legs, and `build()` refuses to write anything unless the stated corrections sum to
minus the control total.

**Corrections are derived, not subtracted.** Each scenario states its correction from its own
parameters -- a rate difference, a withholding percentage -- rather than from the difference
between the two books. Subtracting the books would make the closure assertion an identity that
holds whatever the books contain; deriving it independently is what gives the assertion teeth.

**One fund, six scenarios, three categories.** Two scenarios per capability the fleet publishes
an investigator for. Three of the six move net assets and three do not, which is realistic and
is the point: a quantity break and a timing difference are real work with no monetary impact.
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nav_sentinel.tools import ecb_fx  # noqa: E402

DATA = Path(__file__).parent / "data"
EVAL = Path(__file__).resolve().parents[1] / "eval"

NAV_DATE = date(2026, 8, 17)        # Monday; the ECB published a rate that day
STALE_DATE = date(2026, 8, 14)      # Friday; the rate an accounting system might reuse
PRIOR_CYCLE = date(2026, 7, 17)     # last month's NAV, for recurrence

CENTS = Decimal("0.01")
UNITS = Decimal("0.0001")
RATE = Decimal("0.00000001")


def money(d: Decimal) -> Decimal:
    return d.quantize(CENTS, rounding=ROUND_HALF_UP)


def qty(d: Decimal) -> Decimal:
    return d.quantize(UNITS, rounding=ROUND_HALF_UP)


# ----------------------------------------------------------------------- security master
# Identifiers are real, and were wrong before: US0028241000 is Abbott Laboratories (CUSIP
# 002824100), not Ambev, and GB0009252882 is pre-2022 GlaxoSmithKline. `cik` gives
# `verifiable_against: sec_edgar_fixture` an implementation path -- nothing mapped ISIN to CIK,
# so an investigator had no way to reach a filing at all.
SECURITIES = [
    # isin, ticker, name, ccy, country, cik, is_dr, dr_ratio
    ("US0378331005", "AAPL", "Apple Inc.", "USD", "US", 320193, False, None),
    ("US5949181045", "MSFT", "Microsoft Corporation", "USD", "US", 789019, False, None),
    ("US02319V1035", "ABEV", "Ambev S.A. ADR", "USD", "BR", 1565025, True, "1:1"),
    ("US7170811035", "PFE", "Pfizer Inc.", "USD", "US", 78003, False, None),
    ("GB00BN7SWP63", "GSK", "GSK plc", "GBP", "GB", 1131399, False, None),
    ("FR0000121014", "MC", "LVMH Moet Hennessy Louis Vuitton SE", "EUR", "FR", None, False, None),
    ("NL0011821202", "INGA", "ING Groep N.V.", "EUR", "NL", None, False, None),
    ("DE0007236101", "SIE", "Siemens AG", "EUR", "DE", None, False, None),
]
SEC = {s[0]: s for s in SECURITIES}

FUND = {
    "fund_id": "MERID-GEF",
    "name": "Meridian Global Equity Fund",
    "base_currency": "EUR",
    "domicile": "IE",
    "shares_outstanding": "4250000",
    "fee_bps_annual": "75",
}
BASE = FUND["base_currency"]

#: isin, quantity, local price. Both books agree on these unless a scenario says otherwise.
HOLDINGS = [
    ("US0378331005", "185000", "241.50"),   # AAPL  -- stale FX rate
    ("GB00BN7SWP63", "900000", "16.42"),    # GSK   -- inverted cross
    ("US5949181045", "96000", "512.40"),    # MSFT  -- 2:1 split not applied
    ("US02319V1035", "1450000", "2.86"),    # ABEV  -- ADR dividend gross vs net
    ("FR0000121014", "42000", "612.80"),    # LVMH  -- unsettled purchase on top
    ("US7170811035", "400000", "31.04"),    # PFE   -- failed purchase on top
    ("NL0011821202", "620000", "18.94"),    # clean
    ("DE0007236101", "88000", "227.35"),    # clean
]

OPENING_CASH = Decimal("12000000.00")      # EUR, both books

# --- scenario parameters, from which the corrections are derived -----------------------
SPLIT_RATIO = 2                             # MSFT 2:1 effective on the NAV date
DIV_GROSS_PER_SHARE = Decimal("0.175")      # USD, Ambev ADR
DIV_WITHHOLDING_PCT = Decimal("0.15")       # Brazilian, non-reclaimable
LVMH_PENDING_QTY = Decimal(8500)          # trade date NAV_DATE, settles T+2
PFE_FAILED_QTY = Decimal(120000)          # settlement date passed, never delivered


def rate_on(ccy: str, day: date) -> Decimal:
    """Units of `ccy` per one unit of the fund's base currency, via the ECB's EUR cross."""
    if ccy == BASE:
        return Decimal(1)
    local = ecb_fx.latest_rate_on_or_before(ccy, day)
    base = ecb_fx.latest_rate_on_or_before(BASE, day)
    if local is None or base is None:
        raise RuntimeError(f"no ECB rate for {ccy}/{BASE} on {day}")
    return local[1] / base[1]


def position(isin, quantity, price, source, day, *, fx_rate=None, fx_day=None):
    ccy = SEC[isin][3]
    rate = fx_rate if fx_rate is not None else rate_on(ccy, fx_day or day)
    # Quantize the rate *before* valuing, so the stored market value is derivable from the stored
    # rate. Valuing at full precision and storing a rounded rate left the two inconsistent by
    # cents -- which is precisely the defect the per-row assertion exists to catch, and it caught
    # this one.
    rate = rate.quantize(RATE)
    q, p = qty(Decimal(quantity)), Decimal(price)
    return {
        "fund_id": FUND["fund_id"],
        "isin": isin,
        "as_of": day.isoformat(),
        "quantity": str(q),
        "local_price": str(p),
        "local_currency": ccy,
        "fx_rate": str(rate),
        # Stored, and asserted against q * p / rate by tests. Nothing recomputed it before, so
        # corrupting every rate left the whole suite green.
        "market_value_base": str(money(q * p / rate)),
        "source": source,
    }


def cash(movement_id, value_date, ccy, amount, kind, description, source):
    return {
        "movement_id": movement_id,
        "fund_id": FUND["fund_id"],
        "value_date": value_date.isoformat(),
        "currency": ccy,
        "amount": str(money(Decimal(amount))),
        "movement_type": kind,
        "description": description,
        "source": source,
    }


def trade(trade_id, isin, trade_date, settlement_date, side, quantity, price, status):
    return {
        "trade_id": trade_id,
        "fund_id": FUND["fund_id"],
        "isin": isin,
        "trade_date": trade_date.isoformat(),
        "settlement_date": settlement_date.isoformat(),
        "side": side,
        "quantity": str(qty(Decimal(quantity))),
        "price": str(Decimal(price)),
        "currency": SEC[isin][3],
        "status": status,
    }


def build_cycle(day: date, *, recurring_only: bool = False) -> dict:
    """Build one NAV cycle.

    `recurring_only` builds the prior month with just the ADR dividend break, so a recurring
    break can be recognised on the second cycle rather than re-investigated. It recurs in reality
    for the same reason it recurs here: the custodian's gross-vs-net treatment does not change
    between months.
    """
    acc_pos, cus_pos, acc_cash, cus_cash, trades = [], [], [], [], []
    scenarios: list[dict] = []

    # Quantized here, once. Rounding only inside `position()` meant a correction derived from the
    # full-precision rate disagreed with a book valued at the rounded one -- caught by the per-row
    # derivability assertion.
    usd = rate_on("USD", day).quantize(RATE)
    gbp = rate_on("GBP", day).quantize(RATE)

    # ---------------------------------------------------------------- clean holdings
    for isin, quantity, price in HOLDINGS:
        if recurring_only and isin != "US02319V1035":
            continue
        if isin in {"US0378331005", "GB00BN7SWP63", "US5949181045"} and not recurring_only:
            continue  # handled by a scenario below
        acc_pos.append(position(isin, quantity, price, "accounting", day))
        cus_pos.append(position(isin, quantity, price, "custodian", day))

    if day == PRIOR_CYCLE:
        # Once, on the first cycle. A cash balance is cumulative, so booking opening cash again
        # each cycle double-counted it and left every later NAV record disagreeing with the
        # balance detection actually compares.
        acc_cash.append(
            cash("CASH-OPEN", day, BASE, OPENING_CASH, "opening", "Opening cash", "accounting")
        )
        cus_cash.append(
            cash("CASH-OPEN", day, BASE, OPENING_CASH, "opening", "Opening cash", "custodian")
        )

    # ---------------------------------------------- 1. FX_STALE_RATE (AAPL, USD) -----
    # Accounting revalued using Friday's published rate. A revaluation has no contra in assets
    # or liabilities -- its counterpart is unrealised gain, which *is* net assets -- so a single
    # leg is correct here and net assets legitimately move.
    if not recurring_only:
        stale = rate_on("USD", STALE_DATE).quantize(RATE)
        q, p = Decimal(185000), Decimal("241.50")
        acc_pos.append(position("US0378331005", q, p, "accounting", day, fx_rate=stale))
        cus_pos.append(position("US0378331005", q, p, "custodian", day, fx_rate=usd))
        # Derived from the two published rates, not from the two books.
        correction = money(q * p / usd) - money(q * p / stale)
        scenarios.append({
            "scenario": "FX_STALE_RATE",
            "capability": "nav.fx_rate",
            "isin": "US0378331005",
            "incorrect_side": "accounting",
            "root_cause": (
                f"Accounting revalued the USD position at the ECB reference rate for "
                f"{STALE_DATE} ({stale.quantize(RATE)}) instead of {day} "
                f"({usd.quantize(RATE)})."
            ),
            "expected_corrections": [
                {"leg": "securities", "account": "investments_at_market",
                 "currency": BASE, "amount": str(correction)},
            ],
            "verifiable_against": "ecb_fx_reference_rates",
            "evidence_must_cite": ["rate", "rate_date"],
        })

        # ------------------------------------ 2. FX_INVERTED_CROSS (GSK, GBP) --------
        # The classic direction error: GBP per EUR applied where EUR per GBP was needed.
        inverted = (Decimal(1) / gbp).quantize(RATE)
        q, p = Decimal(900000), Decimal("16.42")
        acc_pos.append(position("GB00BN7SWP63", q, p, "accounting", day, fx_rate=inverted))
        cus_pos.append(position("GB00BN7SWP63", q, p, "custodian", day, fx_rate=gbp))
        correction = money(q * p / gbp) - money(q * p / inverted)
        scenarios.append({
            "scenario": "FX_INVERTED_CROSS",
            "capability": "nav.fx_rate",
            "isin": "GB00BN7SWP63",
            "incorrect_side": "accounting",
            "root_cause": (
                f"Accounting inverted the cross: applied {inverted.quantize(RATE)} "
                f"({BASE} per GBP) where {gbp.quantize(RATE)} (GBP per {BASE}) was required."
            ),
            "expected_corrections": [
                {"leg": "securities", "account": "investments_at_market",
                 "currency": BASE, "amount": str(correction)},
            ],
            "verifiable_against": "ecb_fx_reference_rates",
            "evidence_must_cite": ["rate", "rate_date"],
        })

        # ------------------------- 3. CA_STOCK_SPLIT_NOT_APPLIED (MSFT) --------------
        # Quantity only. No monetary impact, and yet not clearable: a 2x stock-record break
        # drives wrong dividend entitlement and wrong future valuation.
        pre_q, pre_p = Decimal(96000), Decimal("512.40")
        post_q, post_p = pre_q * SPLIT_RATIO, pre_p / SPLIT_RATIO
        acc_pos.append(position("US5949181045", pre_q, pre_p, "accounting", day, fx_rate=usd))
        cus_pos.append(position("US5949181045", post_q, post_p, "custodian", day, fx_rate=usd))
        scenarios.append({
            "scenario": "CA_STOCK_SPLIT_NOT_APPLIED",
            "capability": "nav.corporate_action",
            "isin": "US5949181045",
            "incorrect_side": "accounting",
            "root_cause": (
                f"A {SPLIT_RATIO}:1 share split effective {day} was applied by the custodian and "
                f"not by the accounting book. Quantity differs {SPLIT_RATIO}x; market value "
                f"agrees exactly."
            ),
            "expected_corrections": [
                {"leg": "quantity_restatement", "account": "stock_record",
                 "currency": None, "amount": "0.00", "quantity": str(qty(post_q - pre_q))},
            ],
            "verifiable_against": "sec_edgar_fixture",
            "evidence_must_cite": ["filing"],
        })

    # ---------------------- 4. CA_ADR_DIVIDEND_GROSS_VS_NET (ABEV) -------------------
    # The recurring one. Accounting recognised the gross dividend as cash received; the custodian
    # credited net of Brazilian withholding. The withholding is **non-reclaimable**, so it is an
    # expense and net assets genuinely fall -- it is not a receivable, which would net to zero.
    # An earlier version claimed a receivable while asserting a non-zero correction, which cannot
    # both be true.
    shares = Decimal(1450000)
    gross = money(shares * DIV_GROSS_PER_SHARE)
    withheld = money(gross * DIV_WITHHOLDING_PCT)
    net = gross - withheld
    acc_cash.append(cash("CASH-DIV-ABEV", day, "USD", gross, "dividend",
                         "Ambev ADR dividend, gross", "accounting"))
    cus_cash.append(cash("CASH-DIV-ABEV", day, "USD", net, "dividend",
                         "Ambev ADR dividend, net of 15% withholding", "custodian"))
    # Derived from the declared rate and the withholding percentage.
    # Stated in USD, the currency of the cash account. Presenting it in base would bake in a
    # translation the books perform themselves.
    scenarios.append({
        "scenario": "CA_ADR_DIVIDEND_GROSS_VS_NET",
        "capability": "nav.corporate_action",
        "isin": "US02319V1035",
        "incorrect_side": "accounting",
        "root_cause": (
            f"Accounting recognised the gross ADR dividend of USD {gross} as cash received. The "
            f"custodian credited USD {net}, net of {DIV_WITHHOLDING_PCT:.0%} Brazilian "
            f"withholding of USD {withheld}. The withholding is non-reclaimable under the "
            f"applicable treaty, so it is an expense rather than a receivable and net assets fall."
        ),
        "expected_corrections": [
            {"leg": "cash", "account": "cash_at_bank", "currency": "USD",
             "amount": str(money(-withheld))},
        ],
        "verifiable_against": "sec_edgar_fixture",
        "evidence_must_cite": ["filing", "gross_rate", "withholding_pct"],
        "recurs": True,
    })

    if recurring_only:
        return {
            "day": day,
            "positions": {"accounting": acc_pos, "custodian": cus_pos},
            "cash": {"accounting": acc_cash, "custodian": cus_cash},
            "trades": trades,
            "scenarios": scenarios,
        }

    # ------------------ 5. SETTLE_TRADE_DATE_VS_SETTLEMENT_DATE (LVMH) ---------------
    # A timing difference, not an error. Accounting is *correct* under trade-date accounting; the
    # custodian simply has not settled yet. Both legs are booked -- securities up and cash down --
    # so net assets do not move. Booking only the first leg is exactly the defect that made the
    # declared ground truth explain 2.4% of the NAV difference.
    price = Decimal("612.80")
    consideration = money(LVMH_PENDING_QTY * price)
    settles = day + timedelta(days=2)
    trades.append(trade("TRD-LVMH-01", "FR0000121014", day, settles, "BUY",
                        LVMH_PENDING_QTY, price, "pending"))
    acc_pos.append(position("FR0000121014", LVMH_PENDING_QTY, price, "accounting", day))
    acc_cash.append(cash("CASH-TRD-LVMH-01", day, BASE, -consideration, "settlement",
                         f"LVMH purchase, trade date {day}, settles {settles}", "accounting"))
    scenarios.append({
        "scenario": "SETTLE_TRADE_DATE_VS_SETTLEMENT_DATE",
        "capability": "nav.settlement",
        "isin": "FR0000121014",
        "incorrect_side": "neither",
        "root_cause": (
            f"A purchase of {qty(LVMH_PENDING_QTY)} shares traded {day} settles {settles}. "
            f"Accounting recognises on trade date, correctly; the custodian recognises on "
            f"settlement. Both legs are booked, so net assets are unaffected."
        ),
        "expected_corrections": [],
        "reconciling_item": True,
        "distinguished_by": (
            "settlement_date is in the future, so the trade will settle. Compare "
            "SETTLE_FAILED_TRADE, whose settlement date has passed."
        ),
        "verifiable_against": "books_and_records",
        "evidence_must_cite": ["trade", "settlement_date"],
    })

    # ------------------------------ 6. SETTLE_FAILED_TRADE (PFE) ---------------------
    # An error requiring reversal, not a timing difference. The settlement date has passed and
    # the stock was never delivered, so accounting is carrying a position it does not own. Both
    # legs reverse, so net assets do not move -- but the required action is opposite to
    # scenario 5, and the two are distinguishable from the blotter alone: settlement_date has
    # passed here and is in the future there.
    pfe_price = Decimal("31.04")
    pfe_consideration = money(PFE_FAILED_QTY * pfe_price)
    failed_settles = day - timedelta(days=3)
    trades.append(trade("TRD-PFE-01", "US7170811035", failed_settles - timedelta(days=2),
                        failed_settles, "BUY", PFE_FAILED_QTY, pfe_price, "failed"))
    acc_pos.append(position("US7170811035", PFE_FAILED_QTY, pfe_price, "accounting", day))
    acc_cash.append(cash("CASH-TRD-PFE-01", failed_settles, "USD", -pfe_consideration,
                         "settlement", f"Pfizer purchase, failed settlement {failed_settles}",
                         "accounting"))
    scenarios.append({
        "scenario": "SETTLE_FAILED_TRADE",
        "capability": "nav.settlement",
        "isin": "US7170811035",
        "incorrect_side": "accounting",
        "root_cause": (
            f"A purchase of {qty(PFE_FAILED_QTY)} shares failed to settle on {failed_settles}. "
            f"The stock was never delivered, so accounting is carrying a position the fund does "
            f"not own. Both legs must be reversed."
        ),
        "expected_corrections": [
            {"leg": "securities", "account": "investments_at_market", "currency": "USD",
             "amount": str(money(-PFE_FAILED_QTY * pfe_price))},
            {"leg": "cash", "account": "cash_at_bank", "currency": "USD",
             "amount": str(money(pfe_consideration))},
        ],
        "reconciling_item": False,
        "distinguished_by": (
            "settlement_date has passed and status is failed. Compare "
            "SETTLE_TRADE_DATE_VS_SETTLEMENT_DATE, whose settlement date is in the future."
        ),
        "verifiable_against": "books_and_records",
        "evidence_must_cite": ["trade", "settlement_date"],
    })

    return {
        "day": day,
        "positions": {"accounting": acc_pos, "custodian": cus_pos},
        "cash": {"accounting": acc_cash, "custodian": cus_cash},
        "trades": trades,
        "scenarios": scenarios,
    }


def _to_base(amount: Decimal, ccy: str, day: date) -> Decimal:
    return amount if ccy == BASE else money(amount / rate_on(ccy, day))


def _exposure(positions, movements) -> tuple[Decimal, dict[str, Decimal]]:
    """Base-currency securities total, and cash balances by currency."""
    securities = sum((Decimal(p["market_value_base"]) for p in positions), Decimal(0))
    balances: dict[str, Decimal] = {}
    for m in movements:
        balances[m["currency"]] = balances.get(m["currency"], Decimal(0)) + Decimal(m["amount"])
    return securities, balances


def _net_assets(securities: Decimal, balances: dict[str, Decimal], day: date) -> Decimal:
    """Securities plus cash, translating each currency *balance* once.

    Translating each movement separately and rounding each produces drift against a correction
    that rounds once, which the closure assertion caught on the first run. Real fund accounting
    translates the balance of each currency account, so doing the same is both faithful and exact.
    """
    liquid = sum((_to_base(bal, ccy, day) for ccy, bal in balances.items()), Decimal(0))
    return money(securities + liquid)


def _close(cycle: dict, history: dict[str, list]) -> dict:
    """Assemble the cycle, and refuse to emit it unless posting the corrections closes it.

    The test is not "do the translated corrections sum to minus the control total" -- that
    compares two differently-rounded quantities and fails by a cent on figures that are otherwise
    correct. It is the statement figure 4 actually makes: **post every declared correction to the
    accounting side, recompute net assets, and it must equal the custodian's.**

    That is not an identity. Each correction was derived from its scenario's own parameters -- a
    published rate difference, a withholding percentage, a trade consideration -- not by
    subtracting one book from the other. A recognition missing its contra leg moves net assets by
    an amount no correction accounts for, and this raises.
    """
    day = cycle["day"]
    acc_pos, cus_pos = cycle["positions"]["accounting"], cycle["positions"]["custodian"]
    scenarios = cycle["scenarios"]

    # Positions are a point-in-time snapshot; cash is a running balance. Mixing the two bases is
    # what produced the disagreement between a cycle's NAV record and the balance detection sees.
    acc_sec, acc_bal = _exposure(acc_pos, [m for m in history["accounting"]
                                           if date.fromisoformat(m["value_date"]) <= day])
    cus_sec, cus_bal = _exposure(cus_pos, [m for m in history["custodian"]
                                           if date.fromisoformat(m["value_date"]) <= day])

    acc_net = _net_assets(acc_sec, acc_bal, day)
    cus_net = _net_assets(cus_sec, cus_bal, day)
    control_total = money(acc_net - cus_net)

    # Post the corrections onto a copy of the accounting side.
    fixed_sec, fixed_bal = acc_sec, dict(acc_bal)
    for scenario in scenarios:
        for correction in scenario["expected_corrections"]:
            amount = Decimal(correction["amount"])
            ccy = correction["currency"] or BASE
            if correction["leg"] == "securities":
                fixed_sec += _to_base(amount, ccy, day)
            elif correction["leg"] == "cash":
                fixed_bal[ccy] = fixed_bal.get(ccy, Decimal(0)) + amount
            elif correction["leg"] != "quantity_restatement":
                raise AssertionError(f"unknown correction leg {correction['leg']!r}")

    corrected_net = _net_assets(fixed_sec, fixed_bal, day)
    residual = money(corrected_net - cus_net)
    if residual != Decimal("0.00"):
        raise AssertionError(
            f"{day}: posting the declared corrections does not reconcile the books.\n"
            f"  accounting net assets          {acc_net:>18,}\n"
            f"  custodian net assets           {cus_net:>18,}\n"
            f"  control total                  {control_total:>18,}\n"
            f"  accounting after corrections   {corrected_net:>18,}\n"
            f"  residual                       {residual:>18,}\n"
            f"A non-zero residual means a scenario perturbs net assets by an amount its own "
            f"declared correction does not account for -- most often a recognition booked "
            f"without its contra leg."
        )

    return cycle | {
        "nav": {
            "accounting": _nav_record(day, acc_net, "accounting"),
            "custodian": _nav_record(day, cus_net, "custodian"),
        },
        "control_total": control_total,
    }


def _nav_record(day: date, net_assets: Decimal, source: str) -> dict:
    return {
        "fund_id": FUND["fund_id"],
        "as_of": day.isoformat(),
        "total_assets_base": str(money(net_assets)),
        "total_liabilities_base": "0.00",
        "shares_outstanding": FUND["shares_outstanding"],
        "source": source,
    }


def build() -> tuple[dict, dict]:
    """Build the cycles in sequence, closing each against the ledger as it stands at that date."""
    prior = build_cycle(PRIOR_CYCLE, recurring_only=True)
    current = build_cycle(NAV_DATE)

    # Last month's break was found and corrected after that NAV was signed off, so it does not
    # carry into this month's control total. Without this the current cycle inherits the prior
    # over-recognition and its own scenarios cannot account for it -- which the closure assertion
    # caught. It is also what makes the recurrence story coherent: the break was fixed, and then
    # happened again, because the custodian's gross-vs-net treatment did not change.
    withheld = money(Decimal(1450000) * DIV_GROSS_PER_SHARE * DIV_WITHHOLDING_PCT)
    prior_fix = cash(
        "CASH-ADJ-ABEV-PRIOR", PRIOR_CYCLE + timedelta(days=1), "USD", -withheld,
        "adjustment",
        f"Correction of {PRIOR_CYCLE} ADR withholding break, posted after NAV sign-off",
        "accounting",
    )

    history: dict[str, list] = {"accounting": [], "custodian": []}
    closed = []
    for cycle in (prior, current):
        for side in ("accounting", "custodian"):
            history[side] = history[side] + cycle["cash"][side]
        if cycle is prior:
            closed.append(_close(cycle, history))
            history["accounting"] = history["accounting"] + [prior_fix]
        else:
            closed.append(_close(cycle, history))
    # The adjustment belongs in the emitted ledger, dated between the cycles.
    current["cash"]["accounting"] = [prior_fix, *current["cash"]["accounting"]]
    return closed[0], closed[1]


def write(prior: dict, current: dict, *, check_only: bool = False) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    EVAL.mkdir(parents=True, exist_ok=True)

    def dump(name: str, payload) -> None:
        (DATA / name).write_text(json.dumps(payload, indent=2) + "\n")

    dump("funds.json", [FUND])
    dump("securities.json", [
        {"isin": i, "ticker": t, "name": n, "currency": c, "country": co,
         "cik": cik, "security_type": "equity",
         "is_depositary_receipt": dr, "dr_ratio": ratio}
        for i, t, n, c, co, cik, dr, ratio in SECURITIES
    ])
    # Both cycles in one file per artefact, so recurrence is a date filter rather than a
    # separate fixture set. Detection takes `as_of` explicitly for exactly this reason.
    for side in ("accounting", "custodian"):
        dump(f"positions_{side}.json", prior["positions"][side] + current["positions"][side])
        dump(f"cash_{side}.json", prior["cash"][side] + current["cash"][side])
        dump(f"nav_{side}.json", [prior["nav"][side], current["nav"][side]])
    dump("trades.json", prior["trades"] + current["trades"])

    golden = {
        "fund_id": FUND["fund_id"],
        "base_currency": BASE,
        "cycles": [
            {"nav_date": c["day"].isoformat(),
             "control_total": str(c["control_total"]),
             "scenarios": c["scenarios"]}
            for c in (prior, current)
        ],
        "notes": (
            "Corrections are signed as the amount to add to the accounting book. The generator "
            "refuses to emit a cycle unless *posting* every declared correction onto the "
            "accounting side reconciles it exactly to the custodian's. Each correction is derived "
            "from its scenario's own parameters -- a published rate difference, a withholding "
            "percentage, a trade consideration -- not by subtracting one book from the other, so "
            "that assertion is not an identity.\n\n"
            "Summing the corrections after translating each to base currency is a weaker, "
            "different test and lands within one cent: money(a/r) - money(b/r) is not "
            "money((a-b)/r), and a foreign-currency correction rounds once while the balance it "
            "affects rounds once too. Posting-and-recomputing avoids the double rounding; the "
            "summed form is quoted with a two-cent tolerance and should not be tightened, because "
            "the residue is arithmetic, not error.\n\n"
            "Three of the six scenarios in the current cycle carry no monetary correction: a "
            "quantity-only split, a timing difference that is not an error, and a failed trade "
            "whose two legs net to zero."
        ),
    }
    rendered = yaml.safe_dump(golden, sort_keys=False, width=100)
    target = EVAL / "golden_breaks.yaml"

    if check_only:
        # `--check` exists because regeneration silently overwrote the reviewed ground truth. A
        # judge running `make fixtures` to see how it works would have replaced the file the eval
        # scores against, with no indication anything had changed.
        if not target.exists():
            raise SystemExit(f"{target} does not exist; run without --check to create it.")
        if target.read_text() != rendered:
            raise SystemExit(
                f"{target} differs from what this generator would produce. The ground truth the "
                f"eval scores against has been reviewed, so it is not overwritten silently. Run "
                f"without --check to accept the new version, and review the diff."
            )
        print(f"  {target.name} matches the generator output")
        return

    target.write_text(rendered)


#: Every ECB series the generator asks for. Kept beside the dates it depends on so a cassette
#: refresh cannot miss one -- a miss is a hard failure naming the series, not a silent gap.
def cassette_requests() -> list[tuple[str, str, str]]:
    from nav_sentinel.tools import ecb_fx

    # EUR is short-circuited by the client (rate 1 by definition), and the ECB has no EUR/EUR
    # series -- asking for one 404s.
    currencies = sorted({SEC[isin][3] for isin, _, _ in HOLDINGS} - {"EUR"})
    days = (NAV_DATE, STALE_DATE, PRIOR_CYCLE)
    requests = []
    for day in days:
        # `latest_rate_on_or_before` walks back over weekends and TARGET holidays, so the recorded
        # window has to cover the same lookback the generator will perform.
        start = (day - timedelta(days=14)).isoformat()
        requests.append((ecb_fx._series_key(currencies), start, day.isoformat()))
        for ccy in currencies:
            requests.append((ecb_fx._series_key([ccy]), start, day.isoformat()))
            # `rate_on` uses a 10-day lookback and `latest_rate_on_or_before` a 14-day one, so
            # both windows are recorded or one of them misses.
            requests.append(
                (ecb_fx._series_key([ccy]), (day - timedelta(days=10)).isoformat(),
                 day.isoformat())
            )
    return sorted(set(requests))


def refresh() -> None:
    """Re-record the ECB responses the fixtures need. Requires network access."""
    import os

    from nav_sentinel.tools import ecb_fx

    os.environ["NAV_ECB_LIVE"] = "1"
    ecb_fx._fetch_csv.cache_clear()
    recorded = ecb_fx.refresh_cassette(cassette_requests())
    print(f"  recorded {len(recorded)} ECB responses to {ecb_fx.CASSETTE.name}")


def main() -> None:
    from rich.console import Console
    from rich.table import Table

    console = Console()
    check_only = "--check" in sys.argv
    prior, current = build()
    write(prior, current, check_only=check_only)

    for cycle in (prior, current):
        table = Table(title=f"{FUND['fund_id']} — {cycle['day']}", header_style="bold")
        for col in ("Scenario", "Capability", "Shape", "Correction"):
            table.add_column(col)
        for sc in cycle["scenarios"]:
            legs = sc["expected_corrections"]
            if not legs:
                shape, amount = "reconciling item", "—"
            elif all(leg["leg"] == "quantity_restatement" for leg in legs):
                shape = "quantity only"
                amount = f"{legs[0]['quantity']} shares"
            else:
                shape = f"{len(legs)} leg(s)"
                # Grouped by currency: showing a USD figure under a EUR heading was misleading.
                by_ccy: dict[str, Decimal] = {}
                for leg in legs:
                    ccy = leg["currency"] or BASE
                    by_ccy[ccy] = by_ccy.get(ccy, Decimal(0)) + Decimal(leg["amount"])
                amount = " · ".join(f"{money(v):,} {c}" for c, v in sorted(by_ccy.items()))
            table.add_row(sc["scenario"], sc["capability"], shape, amount)
        console.print(table)
        console.print(
            f"  control total {cycle['control_total']:,} {BASE}  ·  "
            f"stated corrections close it to 0.00\n"
        )


if __name__ == "__main__":
    if "--refresh-rates" in sys.argv:
        refresh()
    main()
