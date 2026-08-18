"""Invariants of the control plane. These are the controls that make the fleet deployable;
if any of them regresses the project is no longer safe to run against a real fund."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from nav_sentinel.control_plane import gateway, identity, policies
from nav_sentinel.tools import catalogue
from nav_sentinel.control_plane.policies import PolicyViolation
from nav_sentinel.domain.models import BreakCategory, BreakType, ExceptionCase, ReconciliationBreak
from nav_sentinel.registry import discover
from nav_sentinel.registry.models import load_manifests

NAV_DATE = date(2026, 8, 17)


@pytest.fixture(autouse=True)
def _clear_log():
    gateway.clear_decision_log()
    yield


@pytest.fixture
def case():
    return ExceptionCase(
        case_id="CASE-TEST-0001", fund_id="F1", as_of=NAV_DATE,
        breaks=[
            ReconciliationBreak(
                break_id="BRK-1", fund_id="F1", as_of=NAV_DATE,
                break_type=BreakType.MARKET_VALUE, isin="ISIN1",
                accounting_value=Decimal("1000"), custodian_value=Decimal("900"),
                tolerance_applied=Decimal("1"),
            )
        ],
    )


class TestRegistry:
    def test_every_break_category_has_an_authorised_investigator(self):
        gaps = [cat for cat, ref in discover.coverage().items() if ref is None]
        assert gaps == [], f"no authorised investigator for: {gaps}"

    def test_service_account_ids_fit_google_limit(self):
        for m in load_manifests():
            assert len(m.service_account_id) <= 30, f"{m.agent_id} exceeds the 30-char limit"

    def test_discovery_selects_highest_version(self):
        m = discover.discover_for_category(BreakCategory.CORPORATE_ACTION)
        assert m is not None and m.agent_id == "corporate-actions-investigator"

    def test_unclassified_has_no_specialist(self):
        """Triage handles unclassified work; no investigator should claim it."""
        specialists = [
            m for m in load_manifests()
            if BreakCategory.UNCLASSIFIED in m.handles_categories
            and m.agent_id != "triage-agent"
        ]
        assert specialists == []


class TestNoAgentHoldsPostingAuthority:
    """The hard control. If this test ever passes with a non-empty list, an agent can move
    a fund's NAV without a human, and the project must not ship."""

    def test_no_published_agent_may_post_entries(self):
        offenders = [m.ref for m in load_manifests() if m.authority.may_post_entries]
        assert offenders == [], f"agents holding posting authority: {offenders}"

    def test_no_published_agent_has_autonomous_bps_headroom(self):
        offenders = [
            m.ref for m in load_manifests() if m.authority.max_autonomous_bps > 0.0
        ]
        assert offenders == [], f"agents with autonomous adjustment headroom: {offenders}"

    def test_posting_is_denied_even_with_a_human_approval(self, case):
        """Approval alone is insufficient: the agent must also hold the authority, and none do."""
        fx = discover.get("fx-rates-investigator")
        with identity.acting_as(fx):
            with pytest.raises(PolicyViolation) as exc:
                gateway.authorize_posting(fx, case, human_approval_ref="APPR-123")
        assert exc.value.decision.policy_id == "P-003-NO-AUTONOMOUS-POSTING"


class TestToolAllowlist:
    def test_declared_tool_is_permitted(self):
        fx = discover.get("fx-rates-investigator")
        with identity.acting_as(fx):
            rate = gateway.call_tool("ecb_fx.latest_rate_on_or_before", "EUR", NAV_DATE)
        assert rate == (NAV_DATE, Decimal("1"))

    def test_undeclared_tool_is_denied(self):
        fx = discover.get("fx-rates-investigator")
        with identity.acting_as(fx):
            with pytest.raises(PolicyViolation) as exc:
                gateway.call_tool("books_and_records.cash_movements", "accounting")
        assert exc.value.decision.policy_id == "P-001-TOOL-ALLOWLIST"

    def test_tool_call_without_identity_is_refused(self):
        """No ambient authority: an unattributed tool call fails rather than defaulting."""
        with pytest.raises(identity.IdentityError):
            gateway.call_tool("ecb_fx.rate_on", "USD", NAV_DATE)

    def test_caller_cannot_supply_the_callable(self):
        """B1. The gateway used to take the function as an argument and validate only the
        name, so any callable ran under a declared tool's label while the audit record named
        the label. Resolution now happens in the catalogue and there is no such argument."""
        import inspect

        params = list(inspect.signature(gateway.call_tool).parameters)
        assert params[0] == "tool_name"
        assert "fn" not in params, "call_tool must not accept a callable from the caller"

        fx = discover.get("fx-rates-investigator")
        sentinel = lambda: "arbitrary code executed"  # noqa: E731
        with identity.acting_as(fx):
            # The old exploit: a declared name with an undeclared function. It now runs the
            # catalogue's function and treats the callable as a positional argument, so the
            # swap cannot execute.
            with pytest.raises(TypeError, match="cannot be supplied by the caller"):
                gateway.call_tool("ecb_fx.rate_on", sentinel)

        # Refused before any policy is evaluated, so no misleading ALLOW is recorded.
        gateway.clear_decision_log()
        with identity.acting_as(fx):
            with pytest.raises(TypeError):
                gateway.call_tool("ecb_fx.rate_on", sentinel)
        assert gateway.decision_log() == [], (
            "a rejected call must not leave ALLOW decisions in the audit log"
        )

    def test_unknown_tool_name_is_distinguishable_from_a_denial(self):
        """A typo in a manifest must not read as a permissions problem."""
        fx = discover.get("fx-rates-investigator")
        with identity.acting_as(fx):
            with pytest.raises(catalogue.UnknownTool):
                gateway.call_tool("ecb_fx.no_such_function")

    def test_every_manifest_tool_resolves_in_the_catalogue(self):
        """A manifest may not declare a capability the catalogue cannot resolve."""
        unresolvable = {
            m.ref: [t for t in m.allowed_tools if t not in catalogue.CATALOGUE]
            for m in load_manifests()
        }
        offenders = {k: v for k, v in unresolvable.items() if v}
        assert not offenders, f"manifests declaring phantom tools: {offenders}"

    def test_every_manifest_declares_at_least_one_tool(self):
        for m in load_manifests():
            assert m.allowed_tools, f"{m.ref} declares no tools and could never do work"


class TestDataScopeEnforcement:
    """P-006. `data_scopes` was declared in every manifest, read by bootstrap.sh to pick IAM
    roles, and consulted by no runtime check."""

    def test_tool_outside_declared_scope_is_denied(self):
        """cash-fees declares cash_movements but not positions; grant the tool and the scope
        check must still refuse it."""
        cf = discover.get("cash-fees-investigator")
        widened = cf.model_copy(
            update={"allowed_tools": cf.allowed_tools + ["books_and_records.positions"]}
        )
        decision = policies.tool_within_data_scope(
            widened, "books_and_records.positions", ("positions",)
        )
        assert not decision.allowed
        assert decision.policy_id == "P-006-DATA-SCOPE"

    def test_declared_scope_is_permitted(self):
        cf = discover.get("cash-fees-investigator")
        assert policies.tool_within_data_scope(
            cf, "books_and_records.cash_movements", ("cash_movements",)
        ).allowed

    def test_external_reference_tools_need_no_internal_scope(self):
        fx = discover.get("fx-rates-investigator")
        assert policies.tool_within_data_scope(fx, "ecb_fx.rate_on", ()).allowed

    def test_every_tool_scope_is_declared_by_its_users(self):
        """Each manifest's tools must be within its own declared scopes -- otherwise the
        manifest is internally inconsistent and P-006 would deny at runtime."""
        problems = []
        for m in load_manifests():
            for name in m.allowed_tools:
                spec = catalogue.CATALOGUE.get(name)
                if spec is None:
                    continue
                missing = [d for d in spec.reads if d not in m.data_scopes.read]
                if missing:
                    problems.append(f"{m.ref}: {name} needs {missing}")
        assert not problems, problems


class TestDraftingAuthority:
    def test_investigators_may_not_draft(self):
        for m in load_manifests():
            if m.agent_id == "remediation-agent":
                continue
            assert not m.authority.may_propose_remediation, f"{m.ref} may draft entries"

    def test_remediation_agent_may_draft(self):
        rem = discover.get("remediation-agent")
        assert gateway.authorize_drafting(rem).allowed


class TestUntrustedIngest:
    def test_agents_reading_the_internet_require_screening(self):
        for m in load_manifests():
            if m.untrusted_inputs:
                assert m.requires_model_armor, (
                    f"{m.ref} ingests untrusted content without Model Armor"
                )

    def test_policy_denies_untrusted_without_armor(self):
        """Constructed case: the policy must refuse the combination, not just the manifests."""
        m = discover.get("corporate-actions-investigator").model_copy(
            update={"requires_model_armor": False}
        )
        decision = policies.untrusted_ingest_requires_armor(m)
        assert not decision.allowed
        assert decision.policy_id == "P-005-UNTRUSTED-INGEST"


class TestDecisionLog:
    def test_decisions_are_recorded_for_audit(self, case):
        fx = discover.get("fx-rates-investigator")
        with identity.acting_as(fx):
            gateway.call_tool("ecb_fx.latest_rate_on_or_before", "EUR", NAV_DATE)
            with pytest.raises(PolicyViolation):
                gateway.authorize_drafting(fx)
        log = gateway.decision_log()
        # P-001 allowlist, P-006 data scope, then the P-002 drafting refusal.
        assert [d.policy_id for d in log] == [
            "P-001-TOOL-ALLOWLIST", "P-006-DATA-SCOPE", "P-002-DRAFT-AUTHORITY",
        ]
        assert [d.effect.value for d in log] == ["allow", "allow", "deny"]
