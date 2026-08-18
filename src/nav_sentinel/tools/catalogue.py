"""The tool catalogue: the only mapping from a tool name to a callable.

Why this exists
---------------
The gateway previously accepted both a tool *name* and the *callable* to run, and validated
only the name. The caller therefore chose both halves independently, so any function could
execute under a declared tool's label -- and the audit record named the label, actively
falsifying the trail. A control that can be defeated by passing a different second argument
is not a control.

Resolution now happens here. `gateway.call_tool` takes a name and nothing else; there is no
argument through which a different function can be supplied.

Each entry also declares the data domains it reads and whether its output is
attacker-controllable, so that scope enforcement and screening are properties of the tool
rather than of an agent's good intentions.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from types import MappingProxyType

from nav_sentinel.registry import discover
from nav_sentinel.tools import books_and_records as bnr
from nav_sentinel.tools import ecb_fx, edgar


@dataclass(frozen=True)
class ToolSpec:
    name: str
    fn: Callable
    reads: tuple[str, ...] = ()
    #: True when the return value is authored outside our trust boundary. The gateway
    #: screens these before they can reach a model context; the agent is not asked to
    #: remember to do it.
    untrusted_output: bool = False
    description: str = ""


_SPECS: tuple[ToolSpec, ...] = (
    # --- authoritative external reference data (structured, not free text) -------------
    ToolSpec("ecb_fx.rate_on", ecb_fx.rate_on, (),
             description="ECB reference rate published for an exact date, or None."),
    ToolSpec("ecb_fx.latest_rate_on_or_before", ecb_fx.latest_rate_on_or_before, (),
             description="Most recent published rate at or before a date, with its date."),
    ToolSpec("ecb_fx.cross_rate", ecb_fx.cross_rate, (),
             description="Correctly-oriented cross rate via EUR."),

    # --- internal books and records, read-only -----------------------------------------
    ToolSpec("books_and_records.funds", bnr.funds, ("funds",)),
    ToolSpec("books_and_records.securities", bnr.securities, ("securities",)),
    ToolSpec("books_and_records.security", bnr.security, ("securities",)),
    ToolSpec("books_and_records.positions", bnr.positions, ("positions",)),
    ToolSpec("books_and_records.cash_movements", bnr.cash_movements, ("cash_movements",)),
    ToolSpec("books_and_records.nav_records", bnr.nav_records, ("nav_records",)),
    ToolSpec("books_and_records.nav_record", bnr.nav_record, ("nav_records",)),
    ToolSpec("books_and_records.trades", bnr.trades, ("trades",)),
    ToolSpec("books_and_records.trades_for_security", bnr.trades_for_security, ("trades",)),

    # --- the registry itself, so triage discovers specialists rather than hard-coding them --
    ToolSpec("registry.discover_for_category", discover.discover_for_category, ("registry",),
             description="Highest-versioned agent declaring support for a break category."),
    ToolSpec("registry.coverage", discover.coverage, ("registry",),
             description="Which categories currently have an authorised investigator."),

    # --- third-party filings: free text, authored by someone else ----------------------
    # Metadata, but still filer-authored: `issuer` comes from the filing's own `name` /
    # `display_names`, and `description` from `primaryDocDescription`, both chosen by the
    # filer. Untrusted for the same reason the document body is.
    ToolSpec("edgar.recent_filings", edgar.recent_filings, (), untrusted_output=True,
             description="Filing metadata for an issuer. Filer-authored strings."),
    ToolSpec("edgar.search_filings", edgar.search_filings, (), untrusted_output=True,
             description="Full-text search across EDGAR. Filer-authored strings."),
    ToolSpec("edgar.fetch_filing_text", edgar.fetch_filing_text, (), untrusted_output=True,
             description="Raw filing text. Attacker-controllable; screened by the gateway."),
)

#: Read-only. A plain dict here would let in-process code swap a spec and run arbitrary
#: code under a declared tool's label -- the original bypass through a different door.
#: Tests that need a different tool use `override()`, which is explicit and scoped.
CATALOGUE: MappingProxyType[str, ToolSpec] = MappingProxyType(
    {spec.name: spec for spec in _SPECS}
)

_overrides: dict[str, ToolSpec] = {}


@contextmanager
def override(name: str, spec: ToolSpec) -> Iterator[None]:
    """Temporarily substitute one tool. For tests only, and deliberately narrow: it is
    scoped, it is named, and it cannot be reached by an agent emitting tool-call data."""
    previous = _overrides.get(name)
    _overrides[name] = spec
    try:
        yield
    finally:
        if previous is None:
            _overrides.pop(name, None)
        else:
            _overrides[name] = previous


class UnknownTool(KeyError):
    """Raised for a name absent from the catalogue.

    Distinct from a policy denial: a denial means the agent may not use a real tool, while
    this means no such tool exists. Collapsing the two would let a typo in a manifest read as
    a permissions problem.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)

    def __str__(self) -> str:
        # KeyError.__str__ applies repr() to its argument, so the message would render
        # wrapped in quotes with escaped inner quotes.
        return self.args[0] if self.args else ""


def resolve(name: str) -> ToolSpec:
    spec = _overrides.get(name) or CATALOGUE.get(name)
    if spec is None:
        raise UnknownTool(
            f"{name!r} is not in the tool catalogue. Add a ToolSpec in "
            f"nav_sentinel.tools.catalogue; it cannot be supplied at call time."
        )
    if spec.name != name:
        # A key that disagrees with its spec would let the audit log name one tool while
        # another ran. Refuse rather than record a false attribution.
        raise UnknownTool(
            f"catalogue key {name!r} maps to a spec named {spec.name!r}; refusing to "
            f"execute under a mismatched label"
        )
    return spec


def names() -> list[str]:
    return sorted(CATALOGUE)


def untrusted_tools() -> list[str]:
    return sorted(n for n, s in CATALOGUE.items() if s.untrusted_output)
