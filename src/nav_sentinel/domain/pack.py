"""The NAV reconciliation process, declared as a pack.

Everything the control plane needs to host this process, and nothing it needs to know about
fund accounting: a capability namespace, a manifest directory, a tool list, and the materiality
thresholds for the unit this process measures impact in.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from nav_sentinel.control_plane.governance import ThresholdSet
from nav_sentinel.control_plane.packs import ProcessPack
from nav_sentinel.tools.catalogue import NAV_TOOLS

KEY = "nav"

#: Root-cause families. Namespaced because a bare "settlement" or "pricing" would collide with
#: any second process that has its own notion of either.
CAPABILITIES: tuple[str, ...] = (
    "nav.fx_rate",
    "nav.corporate_action",
    "nav.pricing",
    "nav.settlement",
    "nav.cash_fees",
    "nav.unclassified",
)

#: Basis points of net asset value. The thresholds a fund administrator would actually set:
#: sub-quarter-bp differences clear themselves, anything above 5bp goes to the CIO.
BPS_THRESHOLDS = ThresholdSet(
    unit="bps",
    auto_clear_below=Decimal("0.25"),
    single_reviewer_below=Decimal(1),
    four_eyes_below=Decimal(5),
)

PACK = ProcessPack(
    key=KEY,
    name="NAV reconciliation",
    capabilities=CAPABILITIES,
    manifest_dir=Path(__file__).parent / "manifests",
    tools=NAV_TOOLS,
    thresholds=(BPS_THRESHOLDS,),
    control_total_unit="base currency",
    notes=(
        "Accounting book against custodian book. The control total is the NAV difference; a "
        "cycle closes when the fleet's corrections account for all of it."
    ),
)
