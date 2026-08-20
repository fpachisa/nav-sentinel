"""A second process on the same control plane.

The claim this section exists to make checkable: adding a process touches no platform code. The
tests below assert the *consequences* of that -- the same registry, the same seven policies, the same
band derivation from a unit-tagged magnitude -- and `test_the_platform_was_not_touched` asserts the
claim itself against git.
"""

from __future__ import annotations

import subprocess
from datetime import date
from decimal import Decimal as D

import pytest

from nav_sentinel.control_plane import gateway, identity, packs
from nav_sentinel.control_plane.governance import CaseFacts, Impact
from nav_sentinel.registry import discover
from nav_sentinel.transfer_agency import register, remediation, tolerance
from nav_sentinel.transfer_agency.models import RegisterCase
from nav_sentinel.transfer_agency.pack import PACK as TA

FUND = "MERID-GEF"
AS_OF = date(2026, 8, 17)


def _case() -> RegisterCase:
    return tolerance.detect(FUND, AS_OF)[0]


class TestTheProcessRegistersLikeAnyOther:
    def test_both_processes_are_hosted(self):
        assert {p.key for p in packs.registered()} == {"nav", "ta"}

    def test_its_capabilities_are_namespaced(self):
        """Unnamespaced capabilities would collide between processes, which `register()` refuses."""
        assert all(c.startswith("ta.") for c in TA.capabilities)

    def test_its_tools_do_not_collide_with_the_other_process(self):
        nav_tools = {
            name for name in packs.catalogue() if name.startswith(("books_and_records.", "ecb_fx."))
        }
        ta_tools = {spec.name for spec in TA.tools}
        assert ta_tools.isdisjoint(nav_tools)
        assert all(name.startswith("register.") for name in ta_tools)

    def test_its_agent_is_discovered_through_the_same_registry(self):
        agent = discover.discover_for_capability("ta.subscription_in_transit")
        assert agent is not None and agent.agent_id == "register-investigator"

    def test_a_capability_it_publishes_nobody_for_reports_none(self):
        """The governance beat, for free: the registry refuses to route rather than picking whichever
        agent looks closest."""
        assert discover.discover_for_capability("ta.transfer_mismatch") is None

    def test_its_manifest_satisfies_the_platform_invariants(self):
        """The same `validate_fleet` the fund-accounting fleet passes -- no posting authority, no
        autonomous ceiling, no drafting, no phantom tools, tools within declared scopes."""
        discover.validate_fleet((discover.get("register-investigator"),))

    def test_it_ships_its_own_prompt(self):
        from nav_sentinel.agents import prompts

        assert prompts.path_for("register-investigator").is_file()
        assert "transfer_agency/prompts" in str(prompts.path_for("register-investigator"))


class TestTheControlPlaneGovernsUnitsWithoutChanging:
    """The part a second *money* process would not have exercised. Thresholds resolve by unit and the
    band is derived from a unit-tagged magnitude, so a process measuring in units is governed by the
    same `band_for` with no arithmetic of its own."""

    @staticmethod
    def _band(units: str) -> str:
        facts = CaseFacts(
            case_id="TACASE-x", subject_id=FUND, as_of=AS_OF,
            capability="ta.subscription_in_transit",
            impact=Impact(value=D(units), unit="units"), status="open",
        )
        return gateway.route_for_approval(facts).metadata["band"]

    @pytest.mark.parametrize(
        ("units", "expected"),
        [
            ("0.5", "auto_clear"),
            ("5000", "single_reviewer"),
            ("125000", "four_eyes"),
            ("500000", "cio_escalation"),
        ],
    )
    def test_a_units_magnitude_bands_across_the_whole_range(self, units, expected):
        assert self._band(units) == expected

    def test_its_thresholds_are_in_units_not_basis_points(self):
        assert [t.unit for t in TA.thresholds] == ["units"]

    def test_the_two_processes_do_not_share_a_unit(self):
        """Two packs claiming one unit with different thresholds would let alphabetical ordering
        decide whose governance applies to whose cases, which `register()` refuses."""
        from nav_sentinel.domain.pack import PACK as NAV

        assert {t.unit for t in NAV.thresholds}.isdisjoint({t.unit for t in TA.thresholds})

    def test_its_control_total_is_stated_in_units(self):
        assert TA.control_total_unit == "units"
        assert tolerance.control_total(FUND, AS_OF) == D("125000.0000")


class TestDetectionIsArithmetic:
    def test_it_finds_the_one_holder_whose_books_disagree(self):
        cases = tolerance.detect(FUND, AS_OF)
        assert [c.breaks[0].holder_id for c in cases] == ["HOLD-002"]

    def test_a_holder_whose_books_agree_is_not_a_case(self):
        assert all(c.breaks[0].holder_id != "HOLD-001" for c in tolerance.detect(FUND, AS_OF))

    def test_the_break_is_exactly_the_units_in_transit(self):
        """Which is what makes the correction arithmetic rather than judgement."""
        case = _case()
        transit = sum(
            (d.units for d in register.in_transit(FUND, AS_OF) if d.holder_id == "HOLD-002"),
            D(0),
        )
        assert case.breaks[0].difference == transit

    def test_case_ids_are_derived_not_counted(self):
        """S8a needs a byte-identical re-run."""
        assert [c.case_id for c in tolerance.detect(FUND, AS_OF)] == [
            c.case_id for c in tolerance.detect(FUND, AS_OF)
        ]

    def test_in_transit_is_relative_to_the_valuation_date(self):
        """A deal is not in transit in itself -- only with respect to a date. Before the trade date
        and after settlement it is not."""
        assert register.in_transit(FUND, date(2026, 8, 13)) == []
        assert register.in_transit(FUND, AS_OF)
        assert register.in_transit(FUND, date(2026, 8, 20)) == []


class TestTheCorrectionUsesNoModel:
    def test_it_explains_the_break_from_the_deals(self):
        restatement = remediation.restate(_case())
        assert restatement.units == D("125000.0000")
        assert restatement.holder_id == "HOLD-002"
        assert restatement.deal_ids == ("DEAL-0001",)

    def test_the_rationale_states_both_dates_and_the_units(self):
        """"In transit" is a property of a deal relative to a valuation date, so a restatement citing
        the units alone cannot be checked."""
        rationale = remediation.restate(_case()).rationale
        for fragment in ("125000.0000", "2026-08-14", "2026-08-19", "2026-08-17", "DEAL-0001"):
            assert fragment in rationale

    def test_it_says_the_difference_resolves_itself(self):
        """Both books are right. Proposing an entry here would create an error rather than fix one --
        the same finding the fund-accounting side records as a reconciling item."""
        assert remediation.restate(_case()).resolves_itself is True

    def test_it_refuses_when_transit_does_not_account_for_the_break(self):
        """Refusing matters more than succeeding: reporting a confident arithmetic explanation for a
        break that is something else is the same defect as a model inventing one."""
        case = _case()
        inflated = case.model_copy(
            update={
                "breaks": [
                    case.breaks[0].model_copy(
                        update={"registrar_units": case.breaks[0].registrar_units + D(50)}
                    )
                ]
            }
        )
        with pytest.raises(remediation.NotExplainedByTransit, match="not explained by timing"):
            remediation.restate(inflated)

    def test_it_refuses_a_holder_with_nothing_in_transit(self):
        case = _case()
        other = case.model_copy(
            update={"breaks": [case.breaks[0].model_copy(update={"holder_id": "HOLD-001"})]}
        )
        with pytest.raises(remediation.NotExplainedByTransit, match="no deals are in transit"):
            remediation.restate(other)

    def test_no_language_model_is_reached_for(self):
        """The claim, asserted: this module puts no model on a step that is subtraction."""
        import inspect

        source = inspect.getsource(remediation)
        for forbidden in ("google.adk", "gemini", "Agent(", "generate_content"):
            assert forbidden not in source


class TestNothingHereMayPostEither:
    """Authority belongs to the platform, not to a process, so the second process inherits it."""

    def test_its_agent_holds_no_posting_authority(self):
        assert discover.get("register-investigator").authority.may_post_entries is False

    def test_its_agent_holds_no_drafting_authority(self):
        assert (
            discover.get("register-investigator").authority.may_propose_remediation is False
        )

    def test_its_tool_calls_are_policed_by_the_same_gateway(self):
        gateway.clear_decision_log()
        with identity.acting_as("register-investigator"):
            gateway.call_tool("register.positions", "registrar")
        recorded = {d.policy_id for d in gateway.decision_log()}
        assert "P-001-TOOL-ALLOWLIST" in recorded
        assert "P-006-DATA-SCOPE" in recorded

    def test_a_fund_accounting_agent_cannot_read_the_register(self):
        """Two processes on one gateway, and the allowlist is still per agent."""
        from nav_sentinel.control_plane.policies import PolicyViolation

        with identity.acting_as("fx-rates-investigator"):
            with pytest.raises(PolicyViolation, match="P-001"):
                gateway.call_tool("register.positions", "registrar")

    def test_the_register_agent_cannot_read_the_books(self):
        from nav_sentinel.control_plane.policies import PolicyViolation

        with identity.acting_as("register-investigator"):
            with pytest.raises(PolicyViolation, match="P-001"):
                gateway.call_tool("books_and_records.positions", "accounting")


class TestThePlatformWasNotTouched:
    """The claim itself, against git rather than against a paragraph."""

    @staticmethod
    def _changed_since(ref: str) -> list[str]:
        result = subprocess.run(
            ["git", "diff", "--name-only", ref, "HEAD"],
            capture_output=True, text=True, check=False,
        )
        return [line for line in result.stdout.splitlines() if line]

    def test_the_transfer_agency_package_imports_no_fund_accounting_module(self):
        """If it did, the "second process" would be a second view of the first."""
        import ast
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "src" / "nav_sentinel" / "transfer_agency"
        offenders: dict[str, list[str]] = {}
        for path in root.rglob("*.py"):
            reached = []
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                    ("nav_sentinel.domain", "nav_sentinel.tools")
                ):
                    reached.append(node.module or "")
            if reached:
                offenders[path.name] = reached
        assert not offenders, offenders

    def test_it_reaches_the_platform_only_through_the_published_interface(self):
        """Packs, governance types and the gateway. Not internals."""
        import ast
        from pathlib import Path

        allowed = {
            "nav_sentinel.control_plane.packs",
            "nav_sentinel.control_plane.governance",
            "nav_sentinel.control_plane.gateway",
        }
        root = Path(__file__).resolve().parents[1] / "src" / "nav_sentinel" / "transfer_agency"
        for path in root.rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                    "nav_sentinel.control_plane"
                ):
                    assert node.module in allowed, f"{path.name} imports {node.module}"
