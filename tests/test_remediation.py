"""Drafting a correction, and the fact that drafting is as far as it goes.

This is the section the project's central claim rests on: a fleet that proposes a correcting entry
which closes the break, and cannot post it. Both halves are asserted here -- the arithmetic of the
proposal, and the denial that survives a valid human approval.
"""

from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal as D

import pytest
from pydantic import ValidationError

from nav_sentinel import composition
from nav_sentinel.agents import remediation
from nav_sentinel.agents.contract import UNKNOWN, Citation, Verdict
from nav_sentinel.agents.remediation import ProposalDraft
from nav_sentinel.control_plane import gateway, identity
from nav_sentinel.control_plane.policies import PolicyViolation
from nav_sentinel.domain import materiality
from nav_sentinel.domain.models import (
    ApprovalClass,
    BreakCategory,
    JournalEntryLine,
    Outcome,
    QuantityRestatementLine,
    RemediationProposal,
)
from nav_sentinel.pipeline import cycle_runner
from nav_sentinel.registry import discover
from nav_sentinel.tools import books_and_records as bnr

AS_OF = date(2026, 8, 17)


def _proposal(**overrides) -> RemediationProposal:
    kwargs = {
        "proposal_id": "PROP-test",
        "outcome": Outcome.JOURNAL_ENTRY,
        "lines": [
            JournalEntryLine(account="investments_at_market", currency="EUR", credit=D("86625.48")),
            JournalEntryLine(account="unrealised_fx", currency="EUR", debit=D("86625.48")),
        ],
        "expected_residual": D(0),
        "rationale": "stale rate",
        "proposed_by_agent": "remediation-agent",
        "proposed_by_version": "1.5.0",
        "requires": ApprovalClass.FOUR_EYES,
    }
    return RemediationProposal(**{**kwargs, **overrides})


def _scored_case(isin: str) -> object:
    case = next(c for c in cycle_runner.detect(AS_OF) if any(b.isin == isin for b in c.breaks))
    materiality.score(
        case,
        bnr.nav_record("custodian", "MERID-GEF", AS_OF),
        cycle_runner._fixture_rates(AS_OF),
    )
    return case


class TestAJournalMustBalanceInEveryCurrency:
    def test_a_balanced_single_currency_entry_is_accepted(self):
        assert _proposal().balances

    def test_cross_currency_netting_is_refused(self):
        """Summing all debits against all credits regardless of currency let an entry with a USD leg
        and an EUR leg net to zero while balancing in neither -- an entry no ledger would accept,
        passing the only arithmetic check there was."""
        with pytest.raises(ValidationError, match="does not balance"):
            _proposal(
                lines=[
                    JournalEntryLine(account="cash_at_bank", currency="USD", debit=D(100)),
                    JournalEntryLine(
                        account="investments_at_market", currency="EUR", credit=D(100)
                    ),
                ]
            )

    def test_the_balance_is_reported_per_currency(self):
        proposal = _proposal(
            lines=[
                JournalEntryLine(account="investments_at_market", currency="EUR", credit=D(10)),
                JournalEntryLine(account="unrealised_fx", currency="EUR", debit=D(10)),
                JournalEntryLine(account="cash_at_bank", currency="USD", debit=D(20)),
                JournalEntryLine(
                    account="withholding_tax_expense", currency="USD", credit=D(20)
                ),
            ]
        )
        assert proposal.balances_by_currency == {"EUR": D(0), "USD": D(0)}

    def test_an_unbalanced_entry_is_refused_at_construction(self):
        with pytest.raises(ValidationError, match="does not balance"):
            _proposal(
                lines=[
                    JournalEntryLine(
                        account="investments_at_market", currency="EUR", credit=D("86625.48")
                    )
                ]
            )

    def test_a_journal_with_no_lines_corrects_nothing(self):
        with pytest.raises(ValidationError, match="corrects nothing"):
            _proposal(lines=[])


class TestNotEveryBreakIsAJournal:
    """Four of the six seeded scenarios are; two are not, and forcing those into a journal shape
    would fabricate an entry for a break that needs none."""

    def test_a_split_is_a_quantity_restatement_with_no_amount(self):
        proposal = _proposal(
            outcome=Outcome.QUANTITY_RESTATEMENT,
            lines=[],
            quantity_lines=[
                QuantityRestatementLine(
                    account="stock_record", isin="US5949181045",
                    from_quantity=D("96000.0000"), to_quantity=D("192000.0000"),
                )
            ],
        )
        assert proposal.quantity_lines[0].delta == D("96000.0000")
        assert proposal.balances

    def test_a_restatement_carrying_money_is_refused(self):
        """A split changes the share count while market value agrees exactly."""
        with pytest.raises(ValidationError, match="moves no money"):
            _proposal(
                outcome=Outcome.QUANTITY_RESTATEMENT,
                quantity_lines=[
                    QuantityRestatementLine(
                        account="stock_record", isin="X", from_quantity=D(1), to_quantity=D(2)
                    )
                ],
            )

    def test_a_restatement_with_no_quantity_lines_restates_nothing(self):
        with pytest.raises(ValidationError, match="restates nothing"):
            _proposal(outcome=Outcome.QUANTITY_RESTATEMENT, lines=[])

    def test_a_timing_difference_posts_nothing(self):
        proposal = _proposal(
            outcome=Outcome.RECONCILING_ITEM, lines=[], expected_residual=D("25737600.00")
        )
        assert proposal.lines == [] and proposal.quantity_lines == []

    def test_a_reconciling_item_that_posts_is_a_contradiction(self):
        """It says both books are right and then corrects one of them."""
        with pytest.raises(ValidationError, match="posts nothing"):
            _proposal(outcome=Outcome.RECONCILING_ITEM)


class TestTheLegsMatchWhatTheGoldenStates:
    """S5 measures *leg-level* accuracy, so the legs are what must line up."""

    def test_an_fx_correction_yields_the_single_nav_leg(self):
        assert _proposal().nav_legs == [
            ("investments_at_market", "EUR", D("-86625.48"))
        ]

    def test_a_two_leg_correction_yields_both(self):
        """Netting per currency collapsed the failed trade's two legs to zero, which would have
        scored a correct entry as having no effect at all."""
        proposal = _proposal(
            lines=[
                JournalEntryLine(
                    account="investments_at_market", currency="USD", credit=D("3724800.00")
                ),
                JournalEntryLine(account="cash_at_bank", currency="USD", debit=D("3724800.00")),
            ],
            requires=ApprovalClass.CIO_ESCALATION,
        )
        assert proposal.nav_legs == [
            ("investments_at_market", "USD", D("-3724800.00")),
            ("cash_at_bank", "USD", D("3724800.00")),
        ]

    def test_the_contra_leg_is_excluded(self):
        """It is a P&L line rather than an asset or liability, so it does not itself appear in net
        assets -- which is why the golden states one leg for an FX correction."""
        assert all(account != "unrealised_fx" for account, _, _ in _proposal().nav_legs)

    def test_every_golden_correction_names_an_account_the_agent_may_use(self):
        """A proposal naming an account no ledger has is unreviewable, so the set is closed -- and
        it has to contain everything the golden expects."""
        from pathlib import Path

        import yaml

        golden = yaml.safe_load(Path("eval/golden_breaks.yaml").read_text())
        expected = {
            correction["account"]
            for cycle in golden["cycles"]
            for scenario in cycle["scenarios"]
            for correction in scenario.get("expected_corrections", [])
        }
        assert expected <= set(remediation.ACCOUNTS), expected - set(remediation.ACCOUNTS)


class TestOnlyTheRemediationAgentDrafts:
    def test_an_investigator_cannot_draft(self):
        """P-002. The investigators report causes."""
        verdict = Verdict(
            case_id="CASE-1", capability="nav.fx_rate", root_cause="stale rate 1.1567",
            confidence=0.9, citations=[Citation(observation_id="OBS-x", relevance="r")],
        )
        with pytest.raises(Exception, match="drafting authority|does not hold"):
            asyncio.run(
                remediation.draft(
                    _scored_case("US0378331005"), verdict, discover.get("fx-rates-investigator")
                )
            )

    def test_drafting_authority_is_recorded_not_assumed(self, monkeypatch):
        """The decision lands in the governance log before a line of the proposal is built."""
        recorded: list[str] = []
        monkeypatch.setattr(
            gateway, "authorize_drafting", lambda: recorded.append("P-002") or None
        )

        async def fake_run(_agent, _case):
            return ProposalDraft(outcome="reconciling_item", rationale="timing")

        monkeypatch.setattr(remediation, "_run", fake_run)
        case = _scored_case("US0378331005")
        case.category = BreakCategory.FX_RATE
        verdict = Verdict(
            case_id=case.case_id, capability="nav.fx_rate", root_cause="stale rate",
            confidence=0.9, citations=[Citation(observation_id="OBS-x", relevance="r")],
        )
        asyncio.run(remediation.draft(case, verdict, discover.get("remediation-agent")))
        assert recorded == ["P-002"]

    def test_a_verdict_asserting_no_cause_is_not_draftable(self):
        """A proposal built on an UNKNOWN root cause is a guess wearing a verdict's clothes."""
        verdict = Verdict(
            case_id="CASE-1", capability="nav.fx_rate", root_cause=UNKNOWN,
            confidence=0.0, citations=[],
        )
        with pytest.raises(remediation.NotDraftable, match="nothing to correct"):
            asyncio.run(
                remediation.draft(
                    _scored_case("US0378331005"), verdict, discover.get("remediation-agent")
                )
            )


class TestTheModelDoesNotSetItsOwnTerms:
    @staticmethod
    def _draft(monkeypatch, drafted: ProposalDraft, isin: str = "US0378331005"):
        async def fake_run(_agent, _case):
            return drafted

        monkeypatch.setattr(remediation, "_run", fake_run)
        case = _scored_case(isin)
        case.category = BreakCategory.FX_RATE
        verdict = Verdict(
            case_id=case.case_id, capability="nav.fx_rate", root_cause="stale rate 1.1567",
            confidence=0.9, citations=[Citation(observation_id="OBS-x", relevance="r")],
        )
        return asyncio.run(remediation.draft(case, verdict, discover.get("remediation-agent")))

    def test_the_approval_band_comes_from_the_control_plane(self, monkeypatch):
        """A proposal that set its own approval level would decide how many humans look at it."""
        proposal = self._draft(
            monkeypatch, ProposalDraft(outcome="reconciling_item", rationale="timing")
        )
        assert proposal.requires is ApprovalClass.FOUR_EYES   # 4.7492bps on this case

    def test_the_draft_schema_has_no_field_for_the_band_or_the_residual(self):
        forbidden = {"requires", "expected_residual", "approval_class", "band"}
        assert forbidden.isdisjoint(ProposalDraft.model_fields)

    def test_an_invented_account_is_rejected_by_the_schema(self):
        """A reviewer cannot check a posting to an account no ledger has."""
        with pytest.raises(ValidationError):
            ProposalDraft.model_validate(
                {
                    "outcome": "journal_entry",
                    "lines": [{"account": "fx_suspense_2", "currency": "EUR", "debit": "1"}],
                }
            )

    def test_the_proposal_id_is_content_derived(self, monkeypatch):
        """S8a needs a byte-identical re-run, which a counter cannot give."""
        drafted = ProposalDraft(outcome="reconciling_item", rationale="timing")
        first = self._draft(monkeypatch, drafted)
        second = self._draft(monkeypatch, drafted)
        assert first.proposal_id == second.proposal_id

    def test_a_different_entry_yields_a_different_id(self, monkeypatch):
        a = self._draft(monkeypatch, ProposalDraft(outcome="reconciling_item", rationale="x"))
        b = self._draft(
            monkeypatch,
            ProposalDraft(
                outcome="quantity_restatement",
                quantity_lines=[
                    {
                        "account": "stock_record", "isin": "US5949181045",
                        "from_quantity": "96000", "to_quantity": "192000",
                    }
                ],
                rationale="split",
            ),
        )
        assert a.proposal_id != b.proposal_id


class TestPostingIsDeniedFourWays:
    """The S4 criterion. Every one of these must hold, and the fourth is the interesting one: an
    approval is necessary and not sufficient."""

    @pytest.fixture
    def case(self):
        scored = _scored_case("US0378331005")
        scored.category = BreakCategory.FX_RATE
        return scored

    def test_no_published_agent_holds_posting_authority(self, case):
        from nav_sentinel.registry.models import load_manifests

        manifests = load_manifests()
        assert manifests
        assert [m.ref for m in manifests if m.authority.may_post_entries] == []

    def test_the_drafting_agent_itself_is_denied(self, case):
        with identity.acting_as("remediation-agent"):
            with pytest.raises(PolicyViolation, match="P-003"):
                remediation.post(_proposal(), case, None)

    def test_a_mutated_manifest_cannot_grant_it(self):
        """The registry's models are frozen, so the attribute assignment fails outright."""
        manifest = discover.get("remediation-agent")
        with pytest.raises(ValidationError):
            manifest.authority.may_post_entries = True

    def test_a_forged_manifest_never_reaches_the_gateway(self):
        """`acting_as` takes a reference and resolves the manifest from the published registry, so a
        caller cannot supply the document describing its own authority."""
        with pytest.raises(Exception, match="not published|unknown|no such agent|refus"):
            with identity.acting_as("i-am-allowed-to-post"):
                pass

    def test_an_invented_approval_reference_does_not_help(self, case):
        with identity.acting_as("remediation-agent"):
            with pytest.raises(PolicyViolation, match="P-003"):
                remediation.post(_proposal(), case, "APPR-0000000000000000")

    def test_a_genuine_approval_does_not_help_either(self, case):
        """The part a slide would skip. The approval is real, recorded, and resolvable -- and
        posting is still refused, because no published agent may post."""
        from nav_sentinel.control_plane.approvals import Principal

        composition.configure(approvals_backend="memory", repository_backend="memory")
        record = composition.approval_authority().grant(
            case.case_id,
            ApprovalClass.FOUR_EYES,
            (Principal(subject="alice", role="controller"), Principal(subject="bob", role="cio")),
        )
        from nav_sentinel.control_plane import approvals

        assert approvals.resolve(record.ref) is not None
        with identity.acting_as("remediation-agent"):
            with pytest.raises(PolicyViolation, match="P-003"):
                remediation.post(_proposal(), case, record.ref)


class TestTheApprovalConsole:
    def test_a_band_refuses_the_wrong_role(self):
        from nav_sentinel.control_plane.approvals import ApprovalDenied, Principal

        composition.configure(approvals_backend="memory", repository_backend="memory")
        with pytest.raises(ApprovalDenied, match="may be signed by"):
            composition.approval_authority().grant(
                "CASE-1", ApprovalClass.FOUR_EYES, (Principal(subject="a", role="reviewer"),)
            )

    def test_four_eyes_refuses_a_single_signer(self):
        from nav_sentinel.control_plane.approvals import ApprovalDenied, Principal

        composition.configure(approvals_backend="memory", repository_backend="memory")
        with pytest.raises(ApprovalDenied, match="distinct signer"):
            composition.approval_authority().grant(
                "CASE-1", ApprovalClass.FOUR_EYES, (Principal(subject="a", role="controller"),)
            )

    def test_the_console_reads_the_repository_not_a_live_process(self):
        """It is a separate entry point, so anything it shows must have been persisted."""
        import inspect

        from nav_sentinel.pipeline import approve_cli

        source = inspect.getsource(approve_cli)
        assert "composition.store()" in source
        assert "cycle_runner.run" not in source

    def test_the_agent_runtime_cannot_mint_an_approval(self):
        """`grant()` was a module function, so anything that could import the module could sign its
        own approval."""
        from nav_sentinel.control_plane import approvals

        assert not hasattr(approvals, "grant")


@pytest.mark.live
class TestDraftingAgainstTheRealModel:
    """Whether the drafted entry is the one the golden expects. Measured 20 August against
    `gemini-3.7-flash`: the FX correction came back as investments_at_market EUR -86,625.48 with an
    unrealised_fx contra, balanced in EUR, requiring four eyes -- the golden's stated correction."""

    @staticmethod
    def _investigate_and_draft(isin: str, category: BreakCategory):
        from nav_sentinel.agents import investigator

        case = _scored_case(isin)
        case.category = category
        agent = discover.discover_for_capability(case.capability)
        verdict, _ = asyncio.run(investigator.investigate(case, agent))
        if not verdict.asserts_a_cause:
            pytest.skip(f"the investigator reached no cause: {verdict.unresolved[:120]}")
        return asyncio.run(
            remediation.draft(case, verdict, discover.get("remediation-agent"))
        )

    def test_the_fx_correction_matches_the_golden_leg(self):
        proposal = self._investigate_and_draft("US0378331005", BreakCategory.FX_RATE)
        assert proposal.balances, proposal.balances_by_currency
        assert ("investments_at_market", "EUR", D("-86625.48")) in proposal.nav_legs, (
            proposal.nav_legs
        )

    def test_the_drafted_entry_balances_and_needs_four_eyes(self):
        proposal = self._investigate_and_draft("US0378331005", BreakCategory.FX_RATE)
        assert proposal.balances
        assert proposal.requires is ApprovalClass.FOUR_EYES
        assert proposal.outcome is Outcome.JOURNAL_ENTRY
