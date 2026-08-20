"""The tools a transfer-agency agent may be granted. Namespaced `register.*`.

Declared here rather than reusing the fund-accounting catalogue, because the two processes share the
control plane and nothing else. Every spec states its source and which facts it can produce, exactly
as the NAV pack's do -- the platform's requirements are process-agnostic, so a second process
satisfies them without a new mechanism.
"""

from __future__ import annotations

from nav_sentinel.control_plane.packs import ToolSpec
from nav_sentinel.transfer_agency import register

_REGISTER = "share_register"
_REGISTER_URI = "register://merian/{tool}/{source}"


def _observe_in_transit(result, args) -> dict:
    """Units dealt but unsettled at the valuation point, and the dates that make them so.

    Both dates, because "in transit" is not a property of the deal -- it is a property of the deal
    *relative to the valuation date*, and a verdict citing the units without them has not shown why
    they are in transit.
    """
    if not result:
        return {}
    return {
        "units": sum(d.units for d in result),
        "trade_date": min(d.trade_date for d in result),
        "settlement_date": min(d.settlement_date for d in result),
        # This tool does take `as_of`, so the fact is producible here. `register.positions` and
        # `register.deals` declared it too and take no such parameter, so `args.get` returned None
        # on every call and `stringify` dropped it -- a declared fact that could never be produced,
        # cited by nothing and noticed by no test.
        "as_of": args.get("as_of"),
    }


def _observe_positions(result, _args) -> dict:
    """A book total across every holder it returned, labelled as such.

    `units` here is the sum over all rows, which for a per-holder break is not the break -- the
    fixture has one holder, so a fund-wide total and one holder's balance were the same number and
    nothing distinguished them. `holders` is recorded beside it so a citation of this observation
    cannot be mistaken for a single holder's position.
    """
    if not result:
        return {}
    return {
        "units": sum(p.units for p in result),
        "holders": len({p.holder_id for p in result}),
    }


def _observe_deals(result, _args) -> dict:
    """Every deal the call returned, aggregated. Deliberately not `_observe_positions`: deals have
    no holder balance, and sharing one function is what let both declare `as_of`."""
    if not result:
        return {}
    return {"units": sum(d.units for d in result), "deals": len(result)}


TA_TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        "register.positions",
        register.positions,
        ("holder_positions",),
        observe=_observe_positions,
        facts=("units", "holders"),
        source=_REGISTER,
        uri_template=_REGISTER_URI,
        description="Unit-holder balances from one book. `source` is 'registrar' or "
                    "'fund_accounting'. Comparing the two is what a register break is.",
    ),
    ToolSpec(
        "register.deals",
        register.deals,
        ("deals",),
        observe=_observe_deals,
        facts=("units", "deals"),
        source=_REGISTER,
        uri_template=_REGISTER_URI,
        description="Every instruction on the register -- subscriptions, redemptions, transfers -- "
                    "with its trade date and settlement date.",
    ),
    ToolSpec(
        "register.in_transit",
        register.in_transit,
        ("deals",),
        observe=_observe_in_transit,
        facts=("units", "trade_date", "settlement_date", "as_of"),
        source=_REGISTER,
        uri_template=_REGISTER_URI,
        description="Deals dealt on or before the valuation date but settling after it. The "
                    "registrar counts these units from the trade date; the fund's unit ledger "
                    "recognises them on settlement, so between the two dates the books differ by "
                    "exactly these units and neither is wrong.",
    ),
)
