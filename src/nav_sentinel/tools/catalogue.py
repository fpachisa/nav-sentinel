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
from nav_sentinel.registry import discover
from nav_sentinel.tools import books_and_records as bnr
from nav_sentinel.tools import ecb_fx, edgar

NAV_TOOLS: tuple[ToolSpec, ...] = (
    # --- authoritative external reference data (structured, not free text) ----------------
    ToolSpec("ecb_fx.rate_on", ecb_fx.rate_on, (),
             description="ECB reference rate published for an exact date, or None."),
    ToolSpec("ecb_fx.latest_rate_on_or_before", ecb_fx.latest_rate_on_or_before, (),
             description="Most recent published rate at or before a date, with its date."),
    ToolSpec("ecb_fx.cross_rate", ecb_fx.cross_rate, (),
             description="Correctly-oriented cross rate via EUR."),

    # --- internal books and records, read-only -------------------------------------------
    ToolSpec("books_and_records.funds", bnr.funds, ("funds",)),
    ToolSpec("books_and_records.securities", bnr.securities, ("securities",)),
    ToolSpec("books_and_records.security", bnr.security, ("securities",)),
    ToolSpec("books_and_records.positions", bnr.positions, ("positions",)),
    ToolSpec("books_and_records.cash_movements", bnr.cash_movements, ("cash_movements",)),
    ToolSpec("books_and_records.nav_records", bnr.nav_records, ("nav_records",)),
    ToolSpec("books_and_records.nav_record", bnr.nav_record, ("nav_records",)),
    ToolSpec("books_and_records.trades", bnr.trades, ("trades",)),
    ToolSpec("books_and_records.trades_for_security", bnr.trades_for_security, ("trades",)),

    # --- the registry itself, so triage discovers specialists rather than hard-coding them -
    ToolSpec("registry.discover_for_capability", discover.discover_for_capability, ("registry",),
             description="Highest-versioned agent declaring support for a capability."),
    ToolSpec("registry.coverage", discover.coverage, ("registry",),
             description="Which capabilities currently have an authorised investigator."),

    # --- third-party filings: free text, authored by someone else -------------------------
    # Metadata, but still filer-authored: `issuer` comes from the filing's own name and
    # `description` from primaryDocDescription, both chosen by the filer.
    ToolSpec("edgar.recent_filings", edgar.recent_filings, (), untrusted_output=True,
             description="Filing metadata for an issuer. Filer-authored strings."),
    ToolSpec("edgar.search_filings", edgar.search_filings, (), untrusted_output=True,
             description="Full-text search across EDGAR. Filer-authored strings."),
    ToolSpec("edgar.fetch_filing_text", edgar.fetch_filing_text, (), untrusted_output=True,
             description="Raw filing text. Attacker-controllable; screened by the gateway."),
)
