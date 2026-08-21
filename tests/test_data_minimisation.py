"""What leaves the fleet, and what an audit record keeps.

The track names "PII leaks" as a thing to guard against, and this use case hands one over: a NAV
error assessment is *about* investors who dealt at the wrong price. The temptation is to fetch them
and be discreet with them. These tests exist because being discreet is an instruction and not a
control -- the first version of `register.dealt_on` returned every matching deal, so the reporting
agent's prompt asked it not to list identities while the tool showed it all of them.
"""

from __future__ import annotations

from datetime import date

import pytest

from nav_sentinel.control_plane import identity, packs
from nav_sentinel.control_plane.observations import stringify
from nav_sentinel.transfer_agency import register

FUND = "MERID-GEF"
DEALING_DATE = date(2026, 8, 14)

#: Fact names that would carry a person or an account rather than a quantity. Not a PII detector:
#: a deliberately small, explicit list of the shapes this domain actually has, so that adding one
#: becomes a visible decision rather than an accident.
IDENTIFYING = frozenset(
    {"holder_id", "holder", "investor", "investor_id", "account", "name", "email", "address"}
)


class TestTheDealingImpactPathCarriesNoIdentities:
    """The capability the remediation office delegates for. Materiality turns on *how many*
    investors were affected, so identities are data no decision here consumes."""

    def test_the_tool_returns_counts_rather_than_deals(self):
        result = register.dealt_on(FUND, DEALING_DATE)
        assert isinstance(result, dict), "returning deals hands the model every holder_id"
        assert set(result) == {"trade_date", "holders", "units", "deals"}

    def test_no_holder_identifier_appears_in_what_the_model_would_see(self):
        """`agent_surface` hands the model `_renderable(result)`, so whatever the tool returns is
        what the model reads. Aggregating downstream of that is the wrong end of the pipe."""
        rendered = str(register.dealt_on(FUND, DEALING_DATE))
        assert "HOLD" not in rendered, rendered

    def test_no_holder_identifier_reaches_the_observation(self):
        spec = packs.catalogue()["register.dealt_on"]
        result = register.dealt_on(FUND, DEALING_DATE)
        projected = stringify(dict(spec.observe(result, {"fund_id": FUND})))
        assert "HOLD" not in str(projected), projected
        assert IDENTIFYING.isdisjoint(projected), projected

    def test_the_count_is_still_correct_after_aggregating(self):
        """Minimisation that loses the answer is not minimisation."""
        result = register.dealt_on(FUND, DEALING_DATE)
        assert result["holders"] == 1
        assert result["deals"] == 1


class TestNoToolQuietlyStartsEmittingIdentifiers:
    def test_no_declared_fact_is_an_identifier(self):
        """Across every registered process and the platform. A projection is what a verdict may
        cite and what lands in Firestore, so an identifier here persists in the audit trail."""
        offenders = {
            name: sorted(IDENTIFYING & set(spec.facts))
            for name, spec in packs.catalogue().items()
            if IDENTIFYING & set(spec.facts)
        }
        assert not offenders, offenders

    def test_the_guard_would_notice_an_identifier(self):
        """A guard that matches nothing passes forever. So this constructs the thing it is meant to
        catch and confirms it is caught -- the first version of this test ended in `or True`, which
        made its own second assertion vacuous."""
        leaky = packs.ToolSpec(
            "register.leaky",
            register.dealt_on,
            ("deals",),
            facts=("holder_id", "units"),
            source="share_register",
            uri_template="register://leak",
            description="a tool that projects an identifier",
        )
        assert IDENTIFYING & set(leaky.facts) == {"holder_id"}


class TestRecalledMemoryHasNoFreeTextSurface:
    """Why the recurrence index is *not* screened by Model Armor, stated as a test rather than a
    comment. Recalled content replayed into a model context is a tool-poisoning surface -- but only
    if it can carry prose. This projection emits integers, an ISO date and a fund id, so there is
    nothing for an injected instruction to survive inside. The moment that stops being true, this
    test fails and the tool needs `untrusted_output=True`."""

    def test_the_projection_emits_no_free_text(self):
        from nav_sentinel.memory import recurrence

        projected = recurrence.observe(
            {"prior_errors": 3, "since": "2026-07-01", "case_ids": ["CASE-REM-1"]},
            {"fund_id": FUND},
        )
        for key, value in projected.items():
            rendered = str(value)
            assert len(rendered) <= 32, f"{key} carries {len(rendered)} characters of text"
            assert " " not in rendered.strip(), f"{key} carries prose: {rendered!r}"

    def test_the_recall_tool_is_not_marked_untrusted(self):
        """Consistent with the above. If it were marked, every offline recall would need Model
        Armor and the offline gate would be a network test."""
        assert packs.catalogue()["memory.prior_errors"].untrusted_output is False


class TestTheReporterCannotReachIdentifyingRowsItDoesNotNeed:
    def test_its_allowlist_excludes_the_holder_level_tools(self):
        """`register.positions` returns holder balances. The reporter has no business with them,
        and the register investigator does -- different capability, different data scope."""
        from nav_sentinel.registry import discover

        reporter = set(discover.get("dealing-impact-reporter").allowed_tools)
        assert "register.dealt_on" in reporter
        assert "register.positions" not in reporter

    def test_the_gateway_refuses_it_at_runtime_too(self):
        """Not just absent from the manifest: refused when attempted."""
        from nav_sentinel.control_plane import gateway
        from nav_sentinel.control_plane.policies import PolicyViolation

        with identity.acting_as("dealing-impact-reporter"), pytest.raises(PolicyViolation):
            gateway.call_tool("register.positions", "registrar", FUND)
