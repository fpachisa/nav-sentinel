"""The transfer-agency process, as the control plane sees it.

One object. Registering it is one line in the composition root and nothing under `registry/`
changes.

The first version of this docstring said nothing under `control_plane/` changed either, and named
`git diff --stat` as the evidence -- while that command showed `control_plane/governance.py` gaining
`CaseBrief`. Adding the *process* costs the composition root; making the investigator process-
agnostic cost one platform type, once. See README defect 11.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from nav_sentinel.control_plane.governance import ThresholdSet
from nav_sentinel.control_plane.packs import ProcessPack
from nav_sentinel.transfer_agency.tools import TA_TOOLS

KEY = "ta"

CAPABILITIES = (
    #: A subscription dealt before the valuation point and settling after it. Arithmetic.
    "ta.subscription_in_transit",
    #: A redemption paid but not struck off the register.
    "ta.redemption_unsettled",
    #: A holder's units moved between accounts. Declared and **published by nobody**, so the
    #: registry reports NONE rather than routing it to whichever agent looks closest.
    "ta.transfer_mismatch",
    #: Who dealt at a published price, and for how many units. Requested by *another process* --
    #: the remediation office, through the gateway -- rather than by anything in this one. The
    #: register can answer it because it records a trade date per deal; the fund's unit ledger
    #: cannot, because it recognises a deal on settlement.
    "ta.dealing_impact",
    "ta.unclassified",
)

#: Thresholds in **units**, not basis points. This is the part of the seam that a second money
#: process would not have exercised: the control plane resolves thresholds *by unit* and derives the
#: band from a unit-tagged magnitude, so a process measuring in units gets governed by the same
#: `band_for` with no arithmetic of its own. A process that declared its own band would be
#: caller-supplied governance.
UNIT_THRESHOLDS = ThresholdSet(
    unit="units",
    auto_clear_below=Decimal(1),
    single_reviewer_below=Decimal(10000),
    four_eyes_below=Decimal(200000),
)

PACK = ProcessPack(
    key=KEY,
    name="Transfer agency",
    capabilities=CAPABILITIES,
    manifest_dir=Path(__file__).parent / "manifests",
    prompt_dir=Path(__file__).parent / "prompts",
    tools=TA_TOOLS,
    thresholds=(UNIT_THRESHOLDS,),
    control_total_unit="units",
    #: A verdict about units in transit must cite the units *and both dates*. "In transit" is a
    #: property of a deal relative to a valuation date, so units alone do not establish it -- the
    #: same shape as the FX rule, where a rate without its date does not establish staleness.
    evidence_requirements=(
        ("ta.subscription_in_transit", ("units", "trade_date", "settlement_date")),
        #: An impact report must name the dealing date it counted, not just a number of holders.
        #: "41 investors" without the date it belongs to is uncheckable, which is the same defect
        #: the FX rule exists for.
        ("ta.dealing_impact", ("holders", "units", "trade_date")),
    ),
    notes=(
        "Reconciles a share register in units. Its subscription-in-transit correction is "
        "arithmetic and says so: a fleet that uses judgement where judgement is required and "
        "deterministic logic where it is not is a better claim than one that puts a model on "
        "every step."
    ),
)
