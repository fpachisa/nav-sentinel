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
from nav_sentinel.tools import corporate_action, ecb_fx, edgar

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


def _observe_latest_rate(result, args) -> dict:
    """`(date, Decimal)`. The date is the whole point: a stale-rate break *is* the gap between the
    rate's publication date and the valuation date, so a verdict citing the rate without it has not
    identified the break."""
    if not result:
        return {}
    rate_date, rate = result
    return {"rate": rate, "rate_date": rate_date, "as_of": args.get("day")}


def _observe_rate(result, args) -> dict:
    """The rate *and the date it was asked for*.

    The date matters as much here as above. Projecting the rate alone let a model cite a rate
    fetched for one date as evidence about another -- the requested date lived only in the
    observation's argument string, which no citation reads.
    """
    if result is None:
        return {}
    return {"rate": result, "rate_date": args.get("day"), "as_of": args.get("day")}


def _observe_cross_rate(result, args) -> dict:
    if result is None:
        return {}
    return {"rate": result, "rate_date": args.get("day"), "as_of": args.get("day")}


def _observe_security(result, _args) -> dict:
    """Currency and country of domicile. The domicile determines the withholding rate a dividend
    should have suffered, which is the fact the corporate-action cross-check turns on."""
    if result is None:
        return {}
    return {
        "currency": getattr(result, "currency", None),
        "domicile": getattr(result, "country", None),
    }


def _observe_nav_record(result, _args) -> dict:
    if result is None:
        return {}
    return {
        "as_of": getattr(result, "as_of", None),
        "amount": getattr(result, "net_assets", None),
    }


def _observe_notice(result, _args) -> dict:
    """Which filing, and the figures it states. `filing` is a citable fact because the golden's
    `evidence_must_cite` names it: for a corporate action, *which document you read* is the first
    thing a reviewer asks."""
    if not result:
        return {}
    return {
        "filing": result.get("filing"),
        "gross_rate": result.get("gross_rate"),
        "withholding_pct": result.get("withholding_pct"),
        "split_ratio": result.get("split_ratio"),
        "currency": result.get("currency"),
    }


def _notice_uri(result) -> str | None:
    return result.get("source_uri") if isinstance(result, dict) else None


def _filing_uri(result) -> str | None:
    """`search_filings` returns a per-filing URI; using it beats a constant that identifies nothing."""
    if isinstance(result, list) and result and isinstance(result[0], dict):
        return result[0].get("source_uri") or result[0].get("url")
    return None


#: Evidence source names, in this process's vocabulary. Declared per tool rather than inferred
#: from the namespace: inference put NAV's own strings inside the platform, so a second process got
#: its bare namespace as a source name and no URI at all.
_ECB = "ecb_fx_reference_rates"
_BOOKS = "books_and_records"
_EDGAR = "sec_edgar"
_REGISTRY = "agent_registry"

#: URIs that identify the *retrieval*, not the service. A constant per namespace named the ECB's
#: data API for every call and gave the books nothing at all, so no books-only investigator could
#: produce a citation with a source. These template on the call's arguments.
_ECB_URI = "https://data-api.ecb.europa.eu/service/data/EXR?currency={currency}&date={day}"
#: Whose books a record came from is material to a reconciliation citation, so the template names
#: it where the tool takes it. `{tool}` is supplied by the platform from the spec's own name.
_BOOKS_URI = "books://merian/{tool}/{source}"
_BOOKS_URI_NO_SOURCE = "books://merian/{tool}"

NAV_TOOLS: tuple[ToolSpec, ...] = (
    # --- authoritative external reference data (structured, not free text) ----------------
    ToolSpec("ecb_fx.rate_on", ecb_fx.rate_on, (),
             observe=_observe_rate, facts=("rate", "rate_date", "as_of"),
             source=_ECB,
             uri_template=_ECB_URI,
             description="ECB reference rate published for an exact date, or None."),
    ToolSpec("ecb_fx.latest_rate_on_or_before", ecb_fx.latest_rate_on_or_before, (),
             observe=_observe_latest_rate, facts=("rate", "rate_date", "as_of"),
             source=_ECB,
             uri_template=_ECB_URI,
             description="Most recent published rate at or before a date, with its date."),
    ToolSpec("ecb_fx.cross_rate", ecb_fx.cross_rate, (),
             observe=_observe_cross_rate, facts=("rate", "rate_date", "as_of"),
             source=_ECB,
             uri_template=_ECB_URI,
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
             source=_BOOKS,
             uri_template=_BOOKS_URI_NO_SOURCE,
             description="Every fund on the platform: id, name, base currency, share class."),
    ToolSpec("books_and_records.securities", bnr.securities, ("securities",),
             source=_BOOKS,
             uri_template=_BOOKS_URI_NO_SOURCE,
             description="The security master: ISIN, name, currency, country of domicile, CIK."),
    ToolSpec("books_and_records.security", bnr.security, ("securities",),
             observe=_observe_security, facts=("currency", "domicile"),
             source=_BOOKS,
             uri_template=_BOOKS_URI_NO_SOURCE,
             description="One security from the master, by ISIN. Returns null if it is not held. "
                         "Use this for a security's country of domicile, which determines the "
                         "withholding rate a dividend should have suffered."),
    ToolSpec("books_and_records.positions", bnr.positions, ("positions",),
             source=_BOOKS,
             uri_template=_BOOKS_URI,
             description="Holdings as at each valuation date: quantity, local-currency price, the "
                         "FX rate applied, and the resulting base-currency market value. "
                         "`source` selects whose books: 'accounting' or 'custodian'. Comparing "
                         "the two is what a position break is."),
    ToolSpec("books_and_records.cash_movements", bnr.cash_movements, ("cash_movements",),
             source=_BOOKS,
             uri_template=_BOOKS_URI,
             description="Cash entries: amount, currency, value date, type and narrative. "
                         "`source` selects whose books: 'accounting' or 'custodian'."),
    ToolSpec("books_and_records.nav_records", bnr.nav_records, ("nav_records",),
             source=_BOOKS,
             uri_template=_BOOKS_URI,
             description="Every published NAV for a fund. `source` is 'accounting' or "
                         "'custodian'. Use this to find which valuation dates exist."),
    ToolSpec("books_and_records.nav_record", bnr.nav_record, ("nav_records",),
             observe=_observe_nav_record, facts=("as_of", "amount"),
             source=_BOOKS,
             uri_template=_BOOKS_URI,
             description="One fund's NAV on one date: total assets, liabilities, shares "
                         "outstanding. `source` is 'accounting' or 'custodian'. Returns null "
                         "when no NAV was struck on that date."),
    ToolSpec("books_and_records.trades", bnr.trades, ("trades",),
             source=_BOOKS,
             uri_template=_BOOKS_URI_NO_SOURCE,
             description="Executed trades: trade date, settlement date, quantity, price. The gap "
                         "between the two dates is what a settlement-timing break turns on."),
    ToolSpec("books_and_records.trades_for_security", bnr.trades_for_security, ("trades",),
             source=_BOOKS,
             uri_template=_BOOKS_URI_NO_SOURCE,
             description="Trades in one security for one fund, by ISIN. Narrower than `trades` "
                         "and the right call when investigating a single holding."),

    # --- corporate actions: the only route to a filing an investigator may take -----------
    #
    # `untrusted_output=False` because what this returns is a typed projection of a
    # `CorporateActionRecord`, not text: declaring it untrusted raises `ContentUnscreenable`,
    # since a record cannot be screened as a string. The screening happens *inside* the tool,
    # through the gateway, against the raw document -- so a P-005 decision is still recorded, and
    # `test_the_notice_path_records_a_screening_decision` fails if that is ever dropped.
    ToolSpec("corporate_action.notice_for", corporate_action.notice_for,
             ("securities", "positions", "cash_movements"),
             observe=_observe_notice,
             facts=("filing", "gross_rate", "withholding_pct", "split_ratio", "currency"),
             source=_EDGAR, locate=_notice_uri,
             description="The corporate-action notice for a security on a date -- action type, "
                         "ex-date, gross rate, withholding percentage and split ratio -- already "
                         "screened, parsed and cross-checked against the books. This is the only "
                         "way to read a filing: the raw document never reaches you."),

    # --- third-party filings: free text, authored by someone else -------------------------
    # Metadata, but still filer-authored: `issuer` comes from the filing's own name and
    # `description` from primaryDocDescription, both chosen by the filer.
    # Only `issuer` and `description` are filer-authored; accession numbers, dates, CIKs and form
    # types are SEC-formatted and cannot carry an instruction. Screening all of them cost 15,000
    # calls for one listing and overflowed the span queue carrying the audit trail.
    ToolSpec("edgar.recent_filings", edgar.recent_filings, (), untrusted_output=True,
             untrusted_fields=("issuer", "description"), source=_EDGAR,
             uri_template="https://data.sec.gov/submissions/CIK{cik}.json",
             description="Filing metadata for an issuer."),
    ToolSpec("edgar.search_filings", edgar.search_filings, (), untrusted_output=True,
             untrusted_fields=("issuer",), source=_EDGAR, locate=_filing_uri,
             uri_template="https://efts.sec.gov/LATEST/search-index?q={query}",
             description="Full-text search across EDGAR."),
    ToolSpec("edgar.fetch_filing_text", edgar.fetch_filing_text, (), untrusted_output=True,
             # The argument *is* the resource, so the citation can name it exactly.
             source=_EDGAR, uri_template="{source_uri}",
             description="Raw filing text. Attacker-controllable; screened by the gateway."),
)
