"""The remediation office, as the control plane sees it.

Measures impact in **affected investors**, not basis points. Two reasons, and the second is the
better one. Fund accounting already owns `bps` and `packs.register` refuses two processes declaring
thresholds for one unit -- but also, a regulator's materiality threshold is genuinely not the fund's
own auto-clear band. Conflating them would be a domain error as well as a registration failure.

It declares one **delegation**: `ta.dealing_impact`. That is the whole coordination surface between
this department and transfer agency. It is declared here, on the pack, rather than in the officer's
manifest -- delegation is a statement about how two departments may interact, and letting one
agent's own document widen it would be the same mistake as letting an agent supply its own manifest.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from nav_sentinel.control_plane.governance import ThresholdSet
from nav_sentinel.control_plane.packs import ProcessPack

KEY = "rem"

CAPABILITIES = (
    #: Is this error material, and does it require compensation. Decided against the regulatory
    #: threshold *and* the fund's recent history -- a fourth pricing error in a quarter is not
    #: treated as a first.
    "rem.materiality",
    #: Declared and published by nobody. A regulator notification is a real stage of this process
    #: and nothing here is authorised to draft one, so the registry reports NONE and the case
    #: escalates to a human rather than being routed to whichever agent looks closest.
    "rem.regulator_notification",
    "rem.unclassified",
)

#: Thresholds in **affected investors**. The count is what a compensation exercise scales with:
#: forty-one investors is forty-one payments to instruct, reconcile and confirm, whatever the
#: per-investor amount turns out to be.
INVESTOR_THRESHOLDS = ThresholdSet(
    unit="investors",
    auto_clear_below=Decimal(1),
    single_reviewer_below=Decimal(10),
    four_eyes_below=Decimal(250),
)

PACK = ProcessPack(
    key=KEY,
    name="Remediation office",
    capabilities=CAPABILITIES,
    manifest_dir=Path(__file__).parent / "manifests",
    prompt_dir=Path(__file__).parent / "prompts",
    tools=(),
    thresholds=(INVESTOR_THRESHOLDS,),
    control_total_unit="investors",
    #: A materiality assessment must cite the recurrence count **and the window it was taken
    #: over**. A bare "three prior errors" is uncheckable; "three since 2026-07-01" can be checked
    #: against the case ids behind it. Same rule as an FX rate without its date.
    evidence_requirements=(("rem.materiality", ("prior_errors", "since")),),
    #: The one thing this department may ask another department for. Transfer agency publishes an
    #: agent for it; this pack never learns which one, because the registry decides that.
    delegations=("ta.dealing_impact",),
    notes=(
        "Coordinates a multi-week NAV error remediation across fund accounting, transfer agency "
        "and oversight. It is a third process rather than a supervisor: the other two stay "
        "isolated and never learn it exists, and its only reach into them is a declared "
        "delegation through the gateway."
    ),
)
