"""The NAV process's tools.

This module now only *declares* specs. Resolution, the immutable view, the override seam and
`UnknownTool` all live in `control_plane.packs`, because the gateway resolving a name by
importing a module full of fund-accounting tools gave it a transitive path to every domain model
in the project.

Each entry declares the data domains it reads and whether its output is attacker-controllable,
so scope enforcement and screening are properties of the tool rather than of an agent's good
intentions.
"""

from __future__ import annotations

from nav_sentinel.control_plane.packs import ToolSpec
from nav_sentinel.tools import books_and_records as bnr
from nav_sentinel.tools import ecb_fx, edgar

# --- what each tool lets a verdict cite -----------------------------------------------------
#
# The NAV process's own vocabulary, and the platform never interprets these keys -- it stringifies
# the mapping and stores it opaquely. The names are the golden file's `evidence_must_cite` entries
# verbatim, so a scenario's stated evidence requirement is checkable by name rather than through a
# mapping nobody maintains.
#
# Per tool rather than generic on purpose: a generic projection would have to guess which attribute
# of a holding is "the rate", and a wrong guess means a verdict cites a number that is not the one
# the tool returned -- the exact failure the citation mechanism exists to prevent.


def _observe_latest_rate(result) -> dict:
    """`(date, Decimal)`. The date is the whole point: a stale-rate break *is* the gap between the
    rate's publication date and the valuation date, so a verdict that cites the rate without it has
    not identified the break."""
    if not result:
        return {}
    rate_date, rate = result
    return {"rate": rate, "rate_date": rate_date}


def _observe_rate(result) -> dict:
    return {"rate": result} if result is not None else {}


def _observe_security(result) -> dict:
    return (
        {"currency": getattr(result, "currency", None), "domicile": getattr(result, "country", None)}
        if result is not None
        else {}
    )


def _observe_nav_record(result) -> dict:
    return (
        {"as_of": getattr(result, "as_of", None), "amount": getattr(result, "net_assets", None)}
        if result is not None
        else {}
    )


NAV_TOOLS: tuple[ToolSpec, ...] = (
    # --- authoritative external reference data (structured, not free text) ----------------
    ToolSpec("ecb_fx.rate_on", ecb_fx.rate_on, (),
             observe=_observe_rate,
             description="ECB reference rate published for an exact date, or None."),
    ToolSpec("ecb_fx.latest_rate_on_or_before", ecb_fx.latest_rate_on_or_before, (),
             observe=_observe_latest_rate,
             description="Most recent published rate at or before a date, with its date."),
    ToolSpec("ecb_fx.cross_rate", ecb_fx.cross_rate, (),
             observe=_observe_rate,
             description="Correctly-oriented cross rate via EUR."),

    # --- internal books and records, read-only -------------------------------------------
    #
    # Every description here says what the tool returns *and* what `source` means, because these
    # nine were all declared without one. A model cannot choose between `positions` and
    # `securities` from their names alone, and it has no way to learn that `source` is one of two
    # specific strings -- so the surface generated from this catalogue would have been unusable by
    # the agent it was generated for. The build-time check in `agent_surface` now refuses an empty
    # description rather than certifying such a surface.
    ToolSpec("books_and_records.funds", bnr.funds, ("funds",),
             description="Every fund on the platform: id, name, base currency, share class."),
    ToolSpec("books_and_records.securities", bnr.securities, ("securities",),
             description="The security master: ISIN, name, currency, country of domicile, CIK."),
    ToolSpec("books_and_records.security", bnr.security, ("securities",),
             observe=_observe_security,
             description="One security from the master, by ISIN. Returns null if it is not held. "
                         "Use this for a security's country of domicile, which determines the "
                         "withholding rate a dividend should have suffered."),
    ToolSpec("books_and_records.positions", bnr.positions, ("positions",),
             description="Holdings as at each valuation date: quantity, local-currency price, the "
                         "FX rate applied, and the resulting base-currency market value. "
                         "`source` selects whose books: 'accounting' or 'custodian'. Comparing "
                         "the two is what a position break is."),
    ToolSpec("books_and_records.cash_movements", bnr.cash_movements, ("cash_movements",),
             description="Cash entries: amount, currency, value date, type and narrative. "
                         "`source` selects whose books: 'accounting' or 'custodian'."),
    ToolSpec("books_and_records.nav_records", bnr.nav_records, ("nav_records",),
             description="Every published NAV for a fund. `source` is 'accounting' or "
                         "'custodian'. Use this to find which valuation dates exist."),
    ToolSpec("books_and_records.nav_record", bnr.nav_record, ("nav_records",),
             observe=_observe_nav_record,
             description="One fund's NAV on one date: total assets, liabilities, shares "
                         "outstanding. `source` is 'accounting' or 'custodian'. Returns null "
                         "when no NAV was struck on that date."),
    ToolSpec("books_and_records.trades", bnr.trades, ("trades",),
             description="Executed trades: trade date, settlement date, quantity, price. The gap "
                         "between the two dates is what a settlement-timing break turns on."),
    ToolSpec("books_and_records.trades_for_security", bnr.trades_for_security, ("trades",),
             description="Trades in one security for one fund, by ISIN. Narrower than `trades` "
                         "and the right call when investigating a single holding."),

    # --- third-party filings: free text, authored by someone else -------------------------
    # Metadata, but still filer-authored: `issuer` comes from the filing's own name and
    # `description` from primaryDocDescription, both chosen by the filer.
    # Only `issuer` and `description` are filer-authored; accession numbers, dates, CIKs and form
    # types are SEC-formatted and cannot carry an instruction. Screening all of them cost 15,000
    # calls for one listing and overflowed the span queue carrying the audit trail.
    ToolSpec("edgar.recent_filings", edgar.recent_filings, (), untrusted_output=True,
             untrusted_fields=("issuer", "description"),
             description="Filing metadata for an issuer."),
    ToolSpec("edgar.search_filings", edgar.search_filings, (), untrusted_output=True,
             untrusted_fields=("issuer",),
             description="Full-text search across EDGAR."),
    ToolSpec("edgar.fetch_filing_text", edgar.fetch_filing_text, (), untrusted_output=True,
             description="Raw filing text. Attacker-controllable; screened by the gateway."),
)
