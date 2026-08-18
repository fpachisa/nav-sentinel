"""Invariants of the control plane. These are the controls that make the fleet deployable;
if any of them regresses the project is no longer safe to run against a real fund."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from nav_sentinel.control_plane import gateway, identity, policies
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
            assert gateway.call_tool("ecb_fx.rate_on", lambda: "ok") == "ok"

    def test_undeclared_tool_is_denied(self):
        fx = discover.get("fx-rates-investigator")
        with identity.acting_as(fx):
            with pytest.raises(PolicyViolation) as exc:
                gateway.call_tool("books_and_records.cash_movements", lambda: "leaked")
        assert exc.value.decision.policy_id == "P-001-TOOL-ALLOWLIST"

    def test_tool_call_without_identity_is_refused(self):
        """No ambient authority: an unattributed tool call fails rather than defaulting."""
        with pytest.raises(identity.IdentityError):
            gateway.call_tool("ecb_fx.rate_on", lambda: "ok")

    def test_every_manifest_declares_at_least_one_tool(self):
        for m in load_manifests():
            assert m.allowed_tools, f"{m.ref} declares no tools and could never do work"


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
            gateway.call_tool("ecb_fx.rate_on", lambda: "ok")
            with pytest.raises(PolicyViolation):
                gateway.authorize_drafting(fx)
        log = gateway.decision_log()
        assert len(log) == 2
        assert [d.effect.value for d in log] == ["allow", "deny"]
