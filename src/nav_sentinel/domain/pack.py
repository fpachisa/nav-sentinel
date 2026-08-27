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

#: Basis points of net asset value, and the reason each boundary sits where it does.
#:
#: A quarter of a basis point is inside the noise of a daily valuation, so those clear themselves.
#: Up to a basis point is one reviewer's judgement. From there to 200bps -- two full percent of the
#: fund -- is a material correction and takes two different signatories. Above 200bps the fund has
#: mis-struck its own price by more than most prospectuses tolerate before an error becomes
#: reportable, and that is the chief investment officer's decision rather than an operational one.
#:
#: 200 rather than the 5 this started with: at 5, six of a normal day's seven exceptions escalated
#: to the CIO, which is not a control -- a threshold everything crosses routes nothing and trains
#: the one person whose attention is scarcest to rubber-stamp.
BPS_THRESHOLDS = ThresholdSet(
    unit="bps",
    auto_clear_below=Decimal("0.25"),
    single_reviewer_below=Decimal(1),
    four_eyes_below=Decimal(200),
)

PACK = ProcessPack(
    key=KEY,
    name="NAV reconciliation",
    capabilities=CAPABILITIES,
    manifest_dir=Path(__file__).parent / "manifests",
    tools=NAV_TOOLS,
    # An internal-only verdict is not corroboration. Both rules say the same thing in this
    # process's own terms: before an investigator may assert *why* the books disagree, it must have
    # checked something outside them.
    #
    # Facts, not tool namespaces. Requiring a namespace only asks that *some* call was made to it:
    # measured, a GBP lookup for an unrelated July date that returned nothing satisfied
    # `("nav.fx_rate", ("ecb_fx",))` while every number in the verdict was invented. Requiring
    # facts means the observation cited has to actually carry them.
    #
    # `nav.fx_rate` -> rate and rate_date, because a stale-rate break *is* the gap between the date
    # a rate belongs to and the date it was applied on. A verdict naming the rate alone has not
    # identified the break, which is why the acceptance criterion names both.
    #
    # `nav.corporate_action` -> the filing it was read from. The golden's `evidence_must_cite`
    # names `filing` for both corporate-action scenarios, and it is the right minimum: a verdict
    # about a dividend or a split that cannot say which document it read has not shown its working.
    #
    # Deliberately not also `gross_rate`. A split notice states no rate, so requiring one would
    # deny every split verdict -- and the whole reason this requirement is declared per capability
    # rather than per scenario is that it has to hold for every case of that capability.
    #
    # `nav.settlement` mandates nothing: a trade-date versus settlement-date break is decided
    # entirely by our own trade records, and inventing a requirement for it would make the rule
    # decorative.
    evidence_requirements=(
        # `currency` is required as well as the rate and its date. Without it a rate lookup for
        # one pair corroborated a claim about another: measured, a GBP lookup returning 0.855
        # satisfied a verdict asserting an invented EUR/USD rate of 9.9999.
        ("nav.fx_rate", ("rate", "rate_date", "currency")),
        ("nav.corporate_action", ("filing",)),
    ),
    thresholds=(BPS_THRESHOLDS,),
    control_total_unit="base currency",
    notes=(
        "Accounting book against custodian book. The control total is the NAV difference; a "
        "cycle closes when the fleet's corrections account for all of it."
    ),
)
