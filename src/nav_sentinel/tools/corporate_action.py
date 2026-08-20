"""One tool for corporate actions: fetch a notice, screen it, extract it, corroborate it.

This replaces giving the corporate-actions investigator the three `edgar` tools, and the reason is
the whole architecture in miniature. That agent is the only one with `untrusted_inputs: true`, and
`edgar.fetch_filing_text`'s own docstring says its return value is attacker-controllable. Handing it
to the agent puts issuer-authored prose directly into a model context and leaves Model Armor as the
only thing between an injected instruction and the reasoning loop -- and this project measured that
filter missing the same injection 0 of 8 times beside one particular filing paragraph. So the
boundary is not the filter. The boundary is that **a model never sees the document at all**.

What crosses instead is a `CorporateActionRecord`: an action type from a closed set, a
pattern-constrained ISIN, a date, two decimals and a ratio matching `\\d{1,6}\\s*:\\s*\\d{1,6}`.
There is no field in it wide enough to carry an instruction.

Four steps, and the order matters:

1. **Fetch** from the committed cassette, so `make eval` runs offline. Live EDGAR is reachable
   behind an environment flag for re-recording, never by default.
2. **Screen** through the gateway, which records a P-005 decision against the acting agent. The
   agent cannot opt out because it never holds the text.
3. **Extract** with the identity *unbound*. `extraction._require_quarantine()` refuses to parse
   while an agent identity is bound, and `gateway.call_tool` runs this function inside
   `acting_as`, so the binding has to be dropped for the length of the parse. `identity.unbound()`
   rather than a fresh `contextvars.Context()`: measured, a fresh context gives a span inside it a
   new trace id with no parent and loses any policy decision recorded there.
4. **Corroborate** against our own books -- the issuer's domicile from the security master, the
   gross rate from the cash ledger. **Never from the agent.** `_cross_check` only runs on the
   arguments it is given, so an omitted argument silently disables it: measured on the poisoned
   notice, omitting both returns `withholding_pct = 0.00` with no exception, while supplying the
   domicile rejects it. Sourcing them here is what makes the control unconditional.

A notice that corroborates nothing is refused. Before the fixture ISINs were corrected, `corroborated`
came back empty for the poisoned *and* the clean notice, which is the one distinction this exists to
make.
"""

from __future__ import annotations

import json
import os
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

from nav_sentinel.control_plane import extraction, gateway, identity
from nav_sentinel.tools import books_and_records as bnr

if TYPE_CHECKING:  # pragma: no cover
    from nav_sentinel.control_plane.extraction import ExtractionOutcome

#: Recorded notices, keyed `ISIN|as_of`. Committed so `make demo`, `make eval` and the tests run
#: with the network unreachable -- the same contract as the ECB cassette.
CASSETTE = Path(__file__).resolve().parents[3] / "fixtures" / "data" / "ca_cassette.json"

FIXTURES = CASSETTE.parent


class NoticeUnavailable(LookupError):
    """No notice is recorded for this security on this date."""


def _use_live() -> bool:
    """Live EDGAR only when explicitly asked for. Offline is the default, not the fallback."""
    return os.environ.get("NAV_EDGAR_LIVE") == "1"


def _cassette() -> dict[str, dict]:
    if not CASSETTE.exists():
        return {}
    return json.loads(CASSETTE.read_text())["notices"]


def notice_for(isin: str, as_of: date) -> dict:
    """The corporate-action notice for a security on a date, typed and corroborated.

    Returns a plain mapping rather than the record object, because this is what a model reads: the
    fields it needs to reason about, plus what the notice was checked against. The typed record is
    what crossed the quarantine; this is a rendering of it.
    """
    key = f"{isin}|{as_of.isoformat()}"
    recorded = _cassette().get(key)
    if recorded is None:
        raise NoticeUnavailable(
            f"no corporate-action notice recorded for {key}. Recorded: "
            f"{sorted(_cassette())}. Re-record with NAV_EDGAR_LIVE=1."
        )

    source_uri = recorded["source_uri"]
    text = (FIXTURES / recorded["document"]).read_text()

    # Screened before anything reads it, through the gateway, so a P-005 decision is recorded
    # against whichever agent called this tool.
    screened = gateway.admit_untrusted_content(text, source_uri=source_uri)

    outcome = _extract_unbound(screened, isin=isin, as_of=as_of, source_uri=source_uri)
    record = outcome.record
    if not outcome.corroborated:
        raise extraction.ExtractionRejected(
            f"the notice for {isin} on {as_of.isoformat()} corroborates nothing against the books, "
            f"so nothing in it can be relied on."
        )

    return {
        "filing": recorded["document"],
        "source_uri": source_uri,
        "action_type": record.action_type,
        "isin": record.isin,
        "ex_date": record.ex_date.isoformat() if record.ex_date else None,
        "gross_rate": record.gross_rate,
        "withholding_pct": record.withholding_pct,
        "split_ratio": record.split_ratio,
        "currency": record.currency,
        "corroborated_against": list(outcome.corroborated),
    }


def _extract_unbound(
    text: str, *, isin: str, as_of: date, source_uri: str
) -> ExtractionOutcome:
    """Parse with no identity bound, and cross-check against our own books.

    `expected_domicile` and `books_gross_rate` are looked up here rather than accepted as
    arguments. That is the difference between a control and a suggestion.
    """
    security = bnr.security(isin)
    with identity.unbound():
        outcome = extraction.extract_corporate_action(
            text,
            isin=isin,
            source_uri=source_uri,
            expected_domicile=getattr(security, "country", None),
            books_gross_rate=_books_gross_rate(isin, as_of),
        )
    return _corroborate_split(outcome, isin=isin, as_of=as_of)


def _corroborate_split(outcome: ExtractionOutcome, *, isin: str, as_of: date) -> ExtractionOutcome:
    """Check a split ratio against the quantity ratio the two books actually show.

    Without this, a split notice corroborated nothing: it states no rate for the books to match and
    no withholding for the treaty schedule to check, so the "refuse when nothing corroborates" rule
    rejected a perfectly good notice. Exempting splits from corroboration was the wrong fix -- the
    ratio *is* checkable, and against the strongest evidence available: 96,000 shares on one book
    against 192,000 on the other is a 2:1 split, stated by the books themselves.
    """
    record = outcome.record
    if record.action_type != "stock_split" or not record.split_ratio:
        return outcome

    quantities = {
        source: next(
            (p.quantity for p in bnr.positions(source) if p.isin == isin and p.as_of == as_of),
            None,
        )
        for source in ("accounting", "custodian")
    }
    if not all(quantities.values()):
        return outcome

    new_shares, _, old_shares = record.split_ratio.partition(":")
    try:
        stated = Decimal(new_shares.strip()) / Decimal(old_shares.strip())
    except (ArithmeticError, ValueError):
        return outcome

    observed = quantities["custodian"] / quantities["accounting"]
    if observed in (stated, 1 / stated):
        outcome.corroborated.append(
            f"split ratio {record.split_ratio} matches the {quantities['accounting']:,} vs "
            f"{quantities['custodian']:,} share difference between the books"
        )
    return outcome


def _books_gross_rate(isin: str, as_of: date) -> Decimal | None:
    """The per-unit gross rate our own books imply for this dividend, or None.

    Cash entries carry no ISIN -- only a `description` like "Ambev ADR dividend, gross" -- so the
    movement is selected by date, type and currency rather than by identifier, and **only when
    exactly one matches**. Ambiguity returns None, which means the withholding cross-check does not
    run, rather than running against a movement that might belong to another holding. A wrong
    cross-check is worse than an absent one: it would corroborate a figure the books never stated.
    Two dividend movements share `CASH-DIV-ABEV` across the July and August cycles, which is why
    the date filter is not optional.
    """
    security = bnr.security(isin)
    if security is None:
        return None
    quantity = next(
        (
            p.quantity
            for p in bnr.positions("accounting")
            if p.isin == isin and p.as_of == as_of
        ),
        None,
    )
    if not quantity:
        return None

    candidates = [
        m
        for m in bnr.cash_movements("accounting")
        if m.value_date == as_of
        and m.movement_type == "dividend"
        and m.currency == security.currency
    ]
    if len(candidates) != 1:
        return None
    return (candidates[0].amount / quantity).quantize(Decimal("0.000001"))


def refresh_cassette() -> dict:
    """Re-record the notice index from the committed fixture documents.

    Deliberately not a live EDGAR fetch. The documents are authored fixtures -- one clean, one
    poisoned, one split, one prior-cycle dividend -- because the poisoned one has to contain an
    injection we control, and no real filing does. `verifiable_against: sec_edgar_fixture` in the
    golden says exactly that, and the URIs are the real EDGAR paths those documents stand in for.
    """
    notices = {
        "US02319V1035|2026-08-17": {
            "document": "ca_notice_abev_clean.txt",
            "source_uri": "https://www.sec.gov/Archives/edgar/data/1565025/abev-20260817.txt",
        },
        "US02319V1035|2026-07-17": {
            "document": "ca_notice_abev_july.txt",
            "source_uri": "https://www.sec.gov/Archives/edgar/data/1565025/abev-20260717.txt",
        },
        "US5949181045|2026-08-17": {
            "document": "ca_notice_msft_split.txt",
            "source_uri": "https://www.sec.gov/Archives/edgar/data/789019/msft-20260817.txt",
        },
        # The adversarial notice, recorded under a date no real cycle uses so it can be driven
        # deliberately without displacing the clean one. Its withholding line says 0.00% and it
        # carries an instruction telling the reader to post the entry without approval.
        "US02319V1035|2026-08-18": {
            "document": "ca_notice_abev_poisoned.txt",
            "source_uri": "https://www.sec.gov/Archives/edgar/data/1565025/abev-20260818.txt",
        },
    }
    CASSETTE.write_text(
        json.dumps(
            {
                "note": (
                    "Corporate-action notices, committed so the fleet runs offline. The documents "
                    "are authored fixtures standing in for the EDGAR paths named here: the "
                    "poisoned variant must contain an injection we control, which no real filing "
                    "does. Regenerate with `make fixtures`."
                ),
                "notices": notices,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    return notices
