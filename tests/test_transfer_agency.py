"""A second process on the same control plane.

The claim this section exists to make checkable: adding a process touches no platform code. The
tests below assert the *consequences* of that -- the same registry, the same seven policies, the same
band derivation from a unit-tagged magnitude -- and `TestThePlatformWasNotTouched` asserts the claim
itself against git, including which single platform file did change and why.
"""

from __future__ import annotations

import subprocess
from datetime import date
from decimal import Decimal as D

import pytest

from nav_sentinel import composition
from nav_sentinel.control_plane import audit, gateway, identity, packs, telemetry
from nav_sentinel.control_plane.governance import CaseFacts, Impact
from nav_sentinel.registry import discover
from nav_sentinel.transfer_agency import register, remediation, tolerance
from nav_sentinel.transfer_agency.models import (
    Deal,
    DealType,
    RegisterBreak,
    RegisterBreakType,
    RegisterCase,
)
from nav_sentinel.transfer_agency.pack import PACK as TA

FUND = "MERID-GEF"
AS_OF = date(2026, 8, 17)
AS_OF_NEXT = date(2026, 8, 19)


#: No process-side package may be imported from another process-side package. `domain` and `tools`
#: were the original two -- if this process reached them, the "second process" would be a second view
#: of the first. `agents` was missing and mattered more: `cycle.py`'s docstring claimed a test forbade
#: importing it, and none did, so the injection that keeps the seam honest was enforced by prose
#: alone. `agents` is a *shared* process-side layer, not a process, and the seam test's
#: `PROCESS_PACKAGES` entry buys only the platform-may-not-reach-it rule -- not this one.
FORBIDDEN_TO_A_PROCESS = (
    "nav_sentinel.domain",
    "nav_sentinel.tools",
    "nav_sentinel.agents",
    "nav_sentinel.pipeline",
    "nav_sentinel.evaluation",
    "nav_sentinel.memory",
)


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

    #: The commit immediately before the transfer-agency process existed. Hardcoded on purpose: the
    #: claim is about a specific span of history, and a test that recomputes its own baseline can be
    #: satisfied by moving the baseline.
    BEFORE_THE_SECOND_PROCESS = "8f40b22"

    @classmethod
    def _changed_since(cls, ref: str) -> list[str] | None:
        """Files changed between `ref` and HEAD, or None if this checkout cannot answer."""
        known = subprocess.run(
            ["git", "cat-file", "-e", f"{ref}^{{commit}}"], capture_output=True, check=False
        )
        if known.returncode != 0:
            return None
        result = subprocess.run(
            # `ref` against the **working tree**, not against HEAD. Comparing to HEAD made this
            # check permanently one commit behind the code it describes: a platform change passed
            # at the moment it was committed and only failed on the *next* run, which is how two
            # files reached the tree unadmitted.
            ["git", "diff", "--name-only", ref],
            capture_output=True,
            text=True,
            check=False,
        )
        return [line for line in result.stdout.splitlines() if line]

    def test_adding_the_second_process_changed_nothing_under_the_registry(self):
        """The headline claim, against git rather than against a paragraph.

        This helper existed with no caller while the module docstring said it "asserts the claim
        itself against git" -- so the one test named for the claim was a dead method and a sentence.
        The narrower claim is the true one: the *registry* is untouched. `control_plane/governance.py`
        gained `CaseBrief`, which README defect 11 records and this test deliberately permits.
        """
        changed = self._changed_since(self.BEFORE_THE_SECOND_PROCESS)
        if changed is None:
            pytest.skip("shallow checkout: the baseline commit is not present")
        assert changed, "the diff is empty, so this test is proving nothing"
        assert [f for f in changed if f.startswith("src/nav_sentinel/registry/")] == []
        assert any(f.startswith("src/nav_sentinel/transfer_agency/") for f in changed)

    #: Platform files that changed after the second process arrived, each with the reason it had to.
    #: A maintained list, not a snapshot: a failure here is a prompt to decide whether a platform
    #: change was really necessary and to record why, which is the discipline the claim rests on.
    #: The first version asserted an exact equality against one file and went stale the moment a
    #: legitimate second change landed -- and it passed at commit time only because `git diff`
    #: reads the *previous* HEAD, so the assertion was one commit behind the tree it described.
    ADMITTED_PLATFORM_CHANGES = {
        # `CaseBrief`, so an investigator takes a flat value instead of fund accounting's case
        # type. And `Lifecycle`, which is process-declared vocabulary the control plane consumes --
        # it belongs beside `ThresholdSet` in the leaf module, and putting it next to the machine
        # that walks it created a real cycle: packs -> casefile -> gateway -> policies -> packs.
        "src/nav_sentinel/control_plane/governance.py",
        # `register` refuses two processes shipping one prompt filename, and one definition of where
        # a pack's templates live. Both are rules *about* hosting processes, not about any process.
        "src/nav_sentinel/control_plane/packs.py",
        # The call counter is readable by the caller, so a span can record calls and observations
        # as the different numbers they are.
        "src/nav_sentinel/control_plane/agent_surface.py",
        # S11. A case that spans weeks needs a stage machine, and the machine is platform while the
        # stages are the process's -- so `casefile` validates transitions a pack declares, exactly
        # as `band_for` derives a band from thresholds a pack declares. And `Repository` gains
        # append-only stage history plus a recurrence lookup, because `save_case` overwrites: a
        # stage machine on top of an overwriting store is a variable that happened to survive.
        "src/nav_sentinel/control_plane/casefile.py",
        "src/nav_sentinel/control_plane/repository.py",
        # P-008 is a policy, so it lives in `policies.py` and the gateway exposes the function that
        # records it -- the shape every other policy here already has. A stage change that left no
        # governance record would be the one state change this project's whole claim says is
        # impossible.
        "src/nav_sentinel/control_plane/policies.py",
        "src/nav_sentinel/control_plane/gateway.py",
    }

    def test_no_platform_file_changed_without_a_recorded_reason(self):
        """The honest form of "adding a process touches no platform code".

        It touched three files, each recorded above and in README defects 11 and 15. What did not
        change is the registry, which the test above asserts separately -- and that is the claim
        worth making, because routing and authority are what a second process would most plausibly
        have needed to bend.
        """
        changed = self._changed_since(self.BEFORE_THE_SECOND_PROCESS)
        if changed is None:
            pytest.skip("shallow checkout: the baseline commit is not present")
        platform = {f for f in changed if f.startswith("src/nav_sentinel/control_plane/")}
        assert platform <= self.ADMITTED_PLATFORM_CHANGES, sorted(
            platform - self.ADMITTED_PLATFORM_CHANGES
        )

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
                    FORBIDDEN_TO_A_PROCESS
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


class TestTheInvestigatorIsReachableAndNotJustPublished:
    """The defect this class exists for: `register-investigator` was published, discoverable,
    `validate_fleet`-clean, allow-listed and **unrunnable**. `investigate()` was annotated with
    fund accounting's `ExceptionCase`, this package may not import `domain`, so no code path could
    hand it a case -- while `make registry` printed the agent beside `ta.subscription_in_transit`
    as though that capability were handled. A registered agent nothing can call is worse than an
    absent one, because the registry advertises it."""

    def test_detect_alone_leaves_nothing_to_route_on(self):
        """The precondition of the bug, kept as a test so the fix cannot be quietly undone.

        `detect` is arithmetic and does not classify. If a future change makes it set a capability,
        the deterministic `classify` step below is what should be doing it.
        """
        assert _case().capability == "ta.unclassified"

    def test_classification_routes_a_transit_break_without_a_model(self):
        from nav_sentinel.transfer_agency import cycle

        assert cycle.classify(_case()).capability == "ta.subscription_in_transit"

    def test_a_partly_explained_break_routes_and_is_then_refused_by_arithmetic(self):
        """Classification asks what kind of break it is; the arithmetic decides whether it closes.

        These were the same predicate once -- `classify` re-derived `restate`'s filter, sum and
        tolerance -- so `restate`'s refusal branch was structurally unreachable and the sentence
        naming the unexplained remainder could never be produced. Putting `raise AssertionError` in
        the cycle's handler left the suite green. The two questions are separate now, and this test
        exists to keep them separate.
        """
        from nav_sentinel.transfer_agency import cycle

        case = _case()
        inflated = case.model_copy(
            update={"breaks": [case.breaks[0].model_copy(update={"registrar_units": D(1625000)})]}
        )
        classified = cycle.classify(inflated)
        assert classified.capability == "ta.subscription_in_transit", "it is still a transit case"

        with pytest.raises(remediation.NotExplainedByTransit) as refused:
            remediation.restate(classified)
        message = str(refused.value)
        assert "125000" in message.replace(",", ""), "it states what transit does explain"
        assert "175000" in message.replace(",", ""), "and what the books actually differ by"
        assert "needs a human" in message

    def test_the_cycles_refusal_branch_is_reachable(self):
        """The handler that was dead code. Reached through `cycle.run`, not by calling `restate`."""
        import asyncio

        from nav_sentinel.transfer_agency import cycle, tolerance

        real_detect = tolerance.detect

        def partly_explained(fund_id, as_of):
            cases = real_detect(fund_id, as_of)
            return [
                c.model_copy(
                    update={
                        "breaks": [c.breaks[0].model_copy(update={"registrar_units": D(1625000)})],
                        "units_at_risk": D(175000),
                    }
                )
                for c in cases
            ]

        async def fake(_brief, _trace_id):
            return "verdict"

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(cycle.tolerance, "detect", partly_explained)
            results = asyncio.run(
                cycle.run(FUND, AS_OF, investigate=fake, routes=lambda _: True, trace=audit.case_trace)
            )

        assert len(results) == 1
        assert not results[0].resolved
        assert "not explained by timing" in results[0].refused

    def test_a_holder_with_no_deals_in_transit_is_not_claimed(self):
        from nav_sentinel.transfer_agency import cycle

        case = _case()
        orphan = case.model_copy(
            update={"breaks": [case.breaks[0].model_copy(update={"holder_id": "HOLD-999"})]}
        )
        assert cycle.classify(orphan).capability == "ta.unclassified"

    def test_the_cycle_hands_the_investigator_a_brief_it_can_read(self):
        """End to end with the model replaced, which is the part that never ran before."""
        import asyncio

        from nav_sentinel.transfer_agency import cycle

        seen = []

        async def fake(brief, _trace_id):
            seen.append(brief)
            return "verdict"

        results = asyncio.run(
            cycle.run(FUND, AS_OF, investigate=fake, routes=lambda _: True, trace=audit.case_trace)
        )
        assert len(seen) == 1, "the investigator was not called"
        brief = seen[0]
        assert brief.capability == "ta.subscription_in_transit"
        assert brief.subject_id == FUND
        assert "125000" in brief.breaks.replace(",", "")
        assert results[0].resolved

    def test_an_unrouted_capability_never_reaches_an_agent(self):
        """`ta.transfer_mismatch` is published by nobody. The cycle must stop, not improvise."""
        import asyncio

        from nav_sentinel.transfer_agency import cycle

        called = []

        async def fake(brief, _trace_id):
            called.append(brief)
            return "verdict"

        results = asyncio.run(cycle.run(FUND, AS_OF, investigate=fake, routes=lambda _: False, trace=audit.case_trace))
        assert called == []
        assert results and not results[0].resolved
        assert "no agent handles" in results[0].refused

    def test_a_units_break_is_never_described_as_money(self):
        """The one place a wrong label would be believed. A register break of 125,000 is units."""
        brief = _case().to_brief()
        assert "units" in brief.breaks
        for symbol in ("EUR", "USD", "GBP", "$", "€"):
            assert symbol not in brief.breaks

    def test_the_band_comes_from_facts_this_process_actually_emits(self):
        """The units banding was previously proved against a hand-built `CaseFacts`, so it showed
        the platform *could* band units, not that this process ever asked it to."""
        from nav_sentinel.transfer_agency import cycle

        facts = cycle.classify(_case()).to_facts()
        assert facts.impact is not None
        assert facts.impact.unit == "units"
        assert gateway.route_for_approval(facts).metadata["band"] == "four_eyes"

    def test_it_runs_the_same_investigator_the_fund_fleet_runs(self):
        """The extensibility claim, as an identity check rather than a paragraph. Not a copy, not a
        subclass -- the same function object.

        Both entry points are named, and that is the whole test. The first version asserted only
        `investigate_cli.investigator.investigate is investigator.investigate`, where both sides
        resolve to the same module attribute and `ta_cli` -- the entry point that actually runs this
        process -- went unmentioned. Swapping `ta_cli`'s investigator for a private copy left the
        suite green, so the one test the README cites by name for this claim proved nothing.
        """
        from nav_sentinel import ta_cli
        from nav_sentinel.agents import investigator
        from nav_sentinel.pipeline import investigate_cli

        assert ta_cli.investigator.investigate is investigator.investigate
        assert investigate_cli.investigator.investigate is investigator.investigate

    def test_the_investigator_imports_no_process_module(self):
        """The structural reason the coupling cannot come back. It was annotation-deep last time --
        `TYPE_CHECKING` only -- which is exactly why nothing caught it."""
        import ast
        from pathlib import Path

        source = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "nav_sentinel"
            / "agents"
            / "investigator.py"
        )
        reached = [
            node.module
            for node in ast.walk(ast.parse(source.read_text()))
            if isinstance(node, ast.ImportFrom)
            and (node.module or "").startswith(
                ("nav_sentinel.domain", "nav_sentinel.tools", "nav_sentinel.transfer_agency")
            )
        ]
        assert not reached, reached

    def test_it_reads_its_own_prompt_and_not_the_fund_fleets(self):
        from nav_sentinel.agents import investigator, prompts

        chosen = prompts.first_available(("register-investigator", "investigator"))
        assert chosen == "register-investigator"
        instruction = investigator._instruction(
            discover.get("register-investigator"), _case().to_brief()
        )
        assert "register" in instruction.lower()
        assert "$" not in instruction, "a placeholder was left unsubstituted"

    def test_an_agent_without_its_own_template_falls_back_to_the_shared_one(self):
        from nav_sentinel.agents import prompts

        assert prompts.first_available(("fx-rates-investigator", "investigator")) == "investigator"


class TestTheArithmeticIsSignedAndDoesNotFabricate:
    """The sign discipline. `in_transit` returns every deal type and both `classify` and `restate`
    summed them with a uniform `+`, so a redemption in transit -- whose difference is *negative*
    because the registrar strikes it off first -- reported `abs(125000 - (-125000))` = 250,000 units
    unexplained, and then told a human "the remaining -250000 is not explained by timing". The
    fixture has one subscription and one holder, so every test passed."""

    @staticmethod
    def _case(registrar: str, ledger: str) -> RegisterCase:
        item = RegisterBreak(
            break_id="TABRK-test",
            fund_id=FUND,
            as_of=AS_OF,
            break_type=RegisterBreakType.HOLDER_BALANCE,
            holder_id="HOLD-002",
            registrar_units=D(registrar),
            ledger_units=D(ledger),
            tolerance_applied=D("0.0001"),
        )
        return RegisterCase(
            case_id="TACASE-test",
            fund_id=FUND,
            as_of=AS_OF,
            breaks=[item],
            units_at_risk=abs(item.difference),
        )

    @staticmethod
    def _deal(deal_id: str, deal_type: DealType, units: str, trade: date, settle: date) -> Deal:
        return Deal(
            deal_id=deal_id,
            fund_id=FUND,
            holder_id="HOLD-002",
            deal_type=deal_type,
            trade_date=trade,
            settlement_date=settle,
            units=D(units),
            source="registrar",
        )

    def test_a_subscription_contributes_positive_units(self):
        deal = self._deal("D", DealType.SUBSCRIPTION, "100", AS_OF, date(2026, 8, 19))
        assert remediation.signed_units(deal) == D(100)

    def test_a_redemption_contributes_negative_units(self):
        deal = self._deal("D", DealType.REDEMPTION, "100", AS_OF, date(2026, 8, 19))
        assert remediation.signed_units(deal) == D(-100)

    def test_a_transfers_direction_is_not_recorded_so_it_is_refused(self):
        """One `holder_id` per deal cannot say whether this holder is source or destination.
        Guessing the sign would be wrong half the time, which is worse than no explanation."""
        deal = self._deal("D", DealType.TRANSFER, "100", AS_OF, date(2026, 8, 19))
        with pytest.raises(remediation.UnsignableDeal):
            remediation.signed_units(deal)

    def test_a_redemption_in_transit_routes_to_its_own_capability(self, monkeypatch):
        """Previously `ta.unclassified`, so README defect 12's stated remedy -- "a fixture, not a
        code change" -- would not have closed it: the agent still would not have run."""
        from nav_sentinel.transfer_agency import cycle

        deals = [self._deal("D-R", DealType.REDEMPTION, "125000", date(2026, 8, 14), AS_OF_NEXT)]
        monkeypatch.setattr(register, "in_transit", lambda _f, _a: deals)
        case = cycle.classify(self._case("1450000", "1575000"))
        assert case.breaks[0].difference == D(-125000)
        assert case.capability == "ta.redemption_unsettled"

        restated = remediation.restate(case)
        assert restated.units == D(-125000)
        # And it must not claim the registrar is the book that is ahead.
        assert "struck them off" in restated.rationale
        assert "the registrar counts them" not in restated.rationale

    def test_deals_that_net_out_are_netted_not_summed(self, monkeypatch):
        """A subscription of 200,000 against a redemption of 75,000 is a difference of 125,000.
        Uniform addition made it 275,000 and refused a break it fully explains."""
        from nav_sentinel.transfer_agency import cycle

        deals = [
            self._deal("D-S", DealType.SUBSCRIPTION, "200000", date(2026, 8, 12), AS_OF_NEXT),
            self._deal("D-R", DealType.REDEMPTION, "75000", date(2026, 8, 13), AS_OF_NEXT),
        ]
        monkeypatch.setattr(register, "in_transit", lambda _f, _a: deals)
        case = cycle.classify(self._case("1575000", "1450000"))
        assert case.capability == "ta.subscription_in_transit"
        assert remediation.restate(case).units == D(125000)

    def test_no_date_pair_is_reported_that_belongs_to_no_deal(self, monkeypatch):
        """`min(trade)` with `min(settlement)` across deals produced "125,000 units subscribed on
        the 10th settle on the 18th" when 25,000 of it settles on the 30th -- a triple belonging to
        no deal, and a reviewer reconciling on the 19th finds 25,000 outstanding."""
        early, late = date(2026, 8, 18), date(2026, 8, 30)
        deals = [
            self._deal("D-A", DealType.SUBSCRIPTION, "25000", date(2026, 8, 10), late),
            self._deal("D-B", DealType.SUBSCRIPTION, "100000", date(2026, 8, 16), early),
        ]
        monkeypatch.setattr(register, "in_transit", lambda _f, _a: deals)
        restated = remediation.restate(self._case("1575000", "1450000"))

        assert restated.clears_on == late, "the whole difference clears at the *last* settlement"
        rationale = restated.rationale
        for deal in deals:
            assert deal.deal_id in rationale, "every deal is named"
            assert deal.settlement_date.isoformat() in rationale, "with its own settlement date"
        assert "125000 units subscribed on 2026-08-10" not in rationale.replace(",", "")


class TestTheSecondProcessProducesAnAuditRecord:
    """The thesis of the whole project is that the audit trail is the deliverable, and the second
    process was the one runnable path that produced none: `ta_cli` never opened a case span, so
    there was no `nav_sentinel.exception_case` root, no `nav.case.*` attributes, and every TA
    observation was recorded with `trace_id=None`. The units *banding* had been held to a higher
    standard than this -- `test_the_band_comes_from_facts_this_process_actually_emits` exists
    precisely because a hand-built `CaseFacts` proves the platform could, not that the process
    asked. The same standard, applied to the audit record."""

    @staticmethod
    def _run(recorder):
        import asyncio

        from nav_sentinel.transfer_agency import cycle

        async def fake(_brief, trace_id):
            recorder["trace_ids"].append(trace_id)
            return "verdict"

        return asyncio.run(
            cycle.run(
                FUND,
                AS_OF,
                investigate=fake,
                routes=lambda _: True,
                trace=recorder["trace"],
            )
        )

    def test_a_case_span_is_opened_with_the_control_planes_own_attribute_names(self, monkeypatch):
        opened: list[dict] = []
        real_span = telemetry.span

        def watch(name, **attributes):
            opened.append({"name": name, **attributes})
            return real_span(name, **attributes)

        monkeypatch.setattr(telemetry, "span", watch)
        recorder = {"trace_ids": [], "trace": audit.case_trace}
        self._run(recorder)

        roots = [s for s in opened if s["name"] == "nav_sentinel.exception_case"]
        assert roots, "the second process opened no case span"
        root = roots[0]
        # The key names are the control plane's, so one governance log covers both processes.
        assert root["nav.case.capability"] == "ta.subscription_in_transit"
        assert root["nav.case.id"].startswith("TACASE-")

    def test_the_band_reaches_the_result_from_the_audit_record_not_a_second_derivation(self):
        """Two derivations meant two identical P-004 decisions per case, already fixed once on the
        fund-accounting side. The band the cycle reports is the one the trace recorded."""
        recorder = {"trace_ids": [], "trace": audit.case_trace}
        results = self._run(recorder)
        assert [r.band for r in results] == ["four_eyes"]

    def test_only_one_approval_route_decision_is_recorded_per_case(self):
        gateway.mark_decisions("ta-audit-test")
        recorder = {"trace_ids": [], "trace": audit.case_trace}
        self._run(recorder)
        routes = [
            d
            for d in gateway.decisions_since("ta-audit-test")
            if d.policy_id.startswith("P-004")
        ]
        assert len(routes) == 1, [d.policy_id for d in routes]

    def test_the_investigator_is_given_the_cases_trace_id(self):
        """Observations recorded under `trace_id=None` cannot be tied back to the case that caused
        them, which is the one thing the audit record exists to do."""
        recorder = {"trace_ids": [], "trace": audit.case_trace}
        self._run(recorder)
        assert len(recorder["trace_ids"]) == 1
        # `None` is what this asserted against before: the parameter existed and nothing filled it.
        assert recorder["trace_ids"][0] is not None

    def test_the_entry_point_actually_supplies_the_tracer(self, monkeypatch):
        """The tests above hand `cycle.run` a tracer themselves, so they prove the cycle traces when
        asked -- not that anything asks. Replacing `ta_cli`'s `trace=audit.case_trace` with a null
        context left all of them green, which is the same shape of hole as the identity test that
        never mentioned `ta_cli`. This one names the wiring.
        """
        import sys

        from nav_sentinel import ta_cli

        captured: dict[str, object] = {}

        async def fake_run(_fund, _as_of, **kwargs):
            captured.update(kwargs)
            return []

        monkeypatch.setattr(ta_cli.cycle, "run", fake_run)
        monkeypatch.setattr(sys, "argv", ["ta_cli", "--as-of", AS_OF.isoformat()])
        ta_cli.main()

        assert captured["trace"] is audit.case_trace, "the entry point supplies no tracer"
        assert captured["routes"]("ta.subscription_in_transit") is True
        assert captured["routes"]("ta.transfer_mismatch") is False, "unrouted must stay unrouted"


class TestTemplateNamesCannotBeHijacked:
    """Templates resolve by filename across every registered process, and `registered()` returns
    packs sorted by key -- so before this check, a pack keyed "aml" shipping `prompts/investigator.md`
    captured the fund fleet's instructions. `register()` already refused colliding tool names and
    colliding threshold units for exactly this reason, and the reason it gave was that alphabetical
    ordering must not decide governance silently."""

    def test_two_processes_shipping_one_template_name_are_refused(self, tmp_path):
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "investigator.md").write_text("HIJACKED TEMPLATE")

        intruder = packs.ProcessPack(
            key="aml",
            name="Anti money laundering",
            capabilities=("aml.screening",),
            manifest_dir=tmp_path / "manifests",
            prompt_dir=prompts_dir,
            tools=(),
            thresholds=(),
            control_total_unit="alerts",
        )
        with pytest.raises(packs.DuplicateProcess) as refused:
            packs.register(intruder)
        assert "investigator.md" in str(refused.value)
        assert "aml" not in {p.key for p in packs.registered()}, "a refused pack must not register"

    def test_a_process_shipping_its_own_template_name_is_accepted(self, tmp_path):
        """The check must not forbid the mechanism it protects: per-agent overrides are the point."""
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "aml-screening-agent.md").write_text("$display_name")

        pack = packs.ProcessPack(
            key="aml",
            name="Anti money laundering",
            capabilities=("aml.screening",),
            manifest_dir=tmp_path / "manifests",
            prompt_dir=prompts_dir,
            tools=(),
            thresholds=(),
            control_total_unit="alerts",
        )
        try:
            packs.register(pack)
            assert "aml" in {p.key for p in packs.registered()}
        finally:
            composition.reset()
            composition.configure()


class TestTheOneHolderAssumptionIsStatedNotAssumed:
    def test_a_multi_break_case_is_refused_rather_than_partly_explained(self):
        """`breaks[0]` was read in three places while `to_brief` rendered all of them to the model
        and `to_facts` reported `item_count`. Nothing constrained the list to one."""
        case = _case()
        doubled = case.model_copy(update={"breaks": [case.breaks[0], case.breaks[0]]})
        with pytest.raises(remediation.NotASingleHolderBreak):
            remediation.restate(doubled)

    def test_the_control_total_counts_every_break_not_the_first_of_each_case(self):
        from nav_sentinel.transfer_agency import tolerance

        assert tolerance.control_total(FUND, AS_OF) == D("125000.0000")

    def test_resolves_itself_discriminates_on_both_branches(self):
        """It cannot be False for anything `restate` builds, since `in_transit` only returns deals
        settling after the valuation point -- so asserting it against a real case compared a constant
        to a constant. Asserted on the dataclass, where both branches exist."""
        leg = remediation.TransitLeg(
            deal_id="D",
            deal_type="subscription",
            units=D(1),
            trade_date=date(2026, 8, 14),
            settlement_date=date(2026, 8, 19),
        )
        pending = remediation.UnitRestatement(
            holder_id="H", units=D(1), as_of=AS_OF, legs=(leg,)
        )
        assert pending.resolves_itself

        settled = remediation.UnitRestatement(
            holder_id="H",
            units=D(1),
            as_of=AS_OF,
            legs=(leg.__class__(**{**leg.__dict__, "settlement_date": date(2026, 8, 15)}),),
        )
        assert not settled.resolves_itself


class TestTheEntryPointRendersWhatTheCycleReturns:
    """631 tests passed while `make ta` died on `AttributeError: 'UnitRestatement' object has no
    attribute 'settlement_date'`. The field had been replaced by `clears_on` -- the fix for a
    fabricated date pair -- and nothing rendered the table, so the only artefact the demo shows was
    covered by no test at all. Family (b) in its purest form: complete coverage of everything except
    the output."""

    def _render(self, monkeypatch, results):
        import sys

        from nav_sentinel import ta_cli

        async def fake_run(_fund, _as_of, **_kwargs):
            return results

        monkeypatch.setattr(ta_cli.cycle, "run", fake_run)
        monkeypatch.setattr(sys, "argv", ["ta_cli", "--as-of", AS_OF.isoformat()])
        ta_cli.main()

    def test_a_resolved_case_renders(self, monkeypatch):
        from nav_sentinel.transfer_agency import cycle

        case = cycle.classify(_case())
        result = cycle.CycleResult(
            case=case,
            band="four_eyes",
            verdict="v",
            restatement=remediation.restate(case),
        )
        self._render(monkeypatch, [result])

    def test_a_refused_case_renders(self, monkeypatch):
        from nav_sentinel.transfer_agency import cycle

        result = cycle.CycleResult(
            case=cycle.classify(_case()),
            band="four_eyes",
            refused="no agent handles ta.transfer_mismatch",
        )
        self._render(monkeypatch, [result])

    def test_an_empty_register_renders(self, monkeypatch):
        self._render(monkeypatch, [])
