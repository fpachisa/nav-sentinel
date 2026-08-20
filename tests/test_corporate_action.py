"""The corporate-action path: the one place an outsider's document reaches this system.

The agent that handles it is the only one with `untrusted_inputs: true`, and Model Armor was
measured missing the same injection 0 of 8 times beside one particular filing paragraph. So the
boundary is not the filter -- it is that a model never sees the document. These tests pin that.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import pytest

from nav_sentinel.control_plane import extraction, gateway, identity, model_armor, packs
from nav_sentinel.control_plane.model_armor import ContentBlocked
from nav_sentinel.registry import discover
from nav_sentinel.tools import corporate_action

#: Captured before anything stubs it, so the live class can restore the real one. Reloading the
#: module instead produced a *new* `ContentBlocked` class, so `pytest.raises` on the imported one
#: never matched and the live test failed while the filter was in fact working.
_REAL_SCREEN = model_armor.screen


@pytest.fixture(autouse=True)
def stub_armor(monkeypatch):
    """Substitute the screener so this module runs offline.

    `notice_for` screens through the gateway, which reaches the real Model Armor service -- so
    without this the whole file needed credentials and network, and took 40 seconds. The offline
    tests are about the *wiring*: that screening happens, that a block propagates intact, and that
    the extractor still refuses when screening does not. Whether the live filter catches this
    particular payload is a separate question, asserted in the `live` class at the bottom.
    """
    from nav_sentinel.control_plane import model_armor

    def fake_screen(text, *, source_uri=None):
        if "IGNORE ALL PREVIOUS INSTRUCTIONS" in text.upper():
            raise model_armor.ContentBlocked(
                model_armor.ArmorVerdict(True, "MATCH_FOUND", ("pi_and_jailbreak",)), source_uri
            )
        return model_armor.ArmorVerdict(False, "NO_MATCH_FOUND")

    monkeypatch.setattr(model_armor, "screen", fake_screen)


ABEV = "US02319V1035"
MSFT = "US5949181045"
CLEAN_DAY = date(2026, 8, 17)
POISONED_DAY = date(2026, 8, 18)


class TestTheInvestigatorCannotReachARawFiling:
    def test_no_edgar_tool_remains_on_the_manifest(self):
        """`fetch_filing_text` returns attacker-controllable prose; `recent_filings` and
        `search_filings` return filer-authored `issuer` and `description` fields. Any of the three
        puts text an outsider wrote into the context of the one agent with untrusted_inputs."""
        allowed = discover.get("corporate-actions-investigator").allowed_tools
        assert not [t for t in allowed if t.startswith("edgar.")], allowed
        assert "corporate_action.notice_for" in allowed

    def test_no_published_agent_may_read_a_raw_filing(self):
        from nav_sentinel.registry.models import load_manifests

        manifests = load_manifests()
        assert manifests, "no manifests loaded; the test would prove nothing"
        for manifest in manifests:
            assert "edgar.fetch_filing_text" not in manifest.allowed_tools, manifest.agent_id

    def test_what_crosses_is_too_narrow_to_carry_an_instruction(self):
        """Every string field on the record is pattern- or length-constrained. There is nowhere in
        it for prose to hide -- which is the actual boundary."""
        fields = extraction.CorporateActionRecord.model_fields
        for name in ("isin", "split_ratio", "currency", "source_uri"):
            constraints = str(fields[name].metadata)
            assert "pattern" in constraints or "max_length" in constraints, name

    def test_the_returned_mapping_carries_no_free_text_from_the_document(self):
        with identity.acting_as("corporate-actions-investigator"):
            notice = corporate_action.notice_for(ABEV, CLEAN_DAY)
        # Every value is a date, a decimal, a closed-set string, a filename, or a URI we supplied.
        assert set(notice) == {
            "filing", "source_uri", "action_type", "isin", "ex_date", "gross_rate",
            "withholding_pct", "split_ratio", "currency", "corroborated_against",
        }
        assert notice["action_type"] in {"cash_dividend", "stock_split", "merger", "unknown"}


class TestScreeningHappensAndIsRecorded:
    def test_the_notice_path_records_a_screening_decision(self):
        """`untrusted_output=False` on the ToolSpec is only honest because the screening happens
        inside the tool. If a future edit drops that call, the flag becomes a lie -- so the P-005
        decision is asserted rather than assumed."""
        gateway.clear_decision_log()
        with identity.acting_as("corporate-actions-investigator"):
            corporate_action.notice_for(ABEV, CLEAN_DAY)
        screening = [
            d for d in gateway.decision_log() if d.policy_id == "P-005-UNTRUSTED-INGEST"
        ]
        assert screening, "the document was read without a screening decision being recorded"

    def test_the_spec_declares_trusted_output_because_a_record_cannot_be_screened(self):
        """Declaring it untrusted raises `ContentUnscreenable`: a typed record is not text."""
        spec = packs.catalogue()["corporate_action.notice_for"]
        assert spec.untrusted_output is False


class TestThePoisonedNoticeIsRefusedTwoWays:
    """Both branches matter. The filter catching it is the first line; the extractor rejecting it
    when the filter misses is why the design does not depend on the filter."""

    def test_model_armor_blocks_it_and_the_block_reaches_the_caller_intact(self):
        gateway.clear_decision_log()
        with identity.acting_as("corporate-actions-investigator"):
            with pytest.raises(ContentBlocked) as raised:
                corporate_action.notice_for(ABEV, POISONED_DAY)
        assert "pi_and_jailbreak" in str(raised.value)
        denials = [d for d in gateway.decision_log() if not d.allowed]
        assert [d.policy_id for d in denials] == ["P-005-UNTRUSTED-INGEST"]

    def test_a_screening_block_is_not_reclassified_as_a_tool_failure(self):
        """It was. Wrapping it made "Model Armor caught an injection in this filing" and "the tool
        crashed" the same finding, and those two are the most important things this path reports."""
        with identity.acting_as("corporate-actions-investigator"):
            with pytest.raises(ContentBlocked):
                gateway.call_tool("corporate_action.notice_for", isin=ABEV, as_of=POISONED_DAY)

    def test_when_screening_misses_it_the_extractor_refuses(self, monkeypatch):
        """The measured case: the same injection was missed 0 of 8 times beside one filing
        paragraph. With the filter neutralised the treaty cross-check still refuses."""
        monkeypatch.setattr(
            gateway, "admit_untrusted_content", lambda text, *, source_uri=None: text
        )
        with identity.acting_as("corporate-actions-investigator"):
            with pytest.raises(extraction.ExtractionRejected, match="0.00%|treaty"):
                corporate_action.notice_for(ABEV, POISONED_DAY)

    def test_the_extractors_refusal_also_reaches_the_caller_intact(self, monkeypatch):
        monkeypatch.setattr(
            gateway, "admit_untrusted_content", lambda text, *, source_uri=None: text
        )
        with identity.acting_as("corporate-actions-investigator"):
            with pytest.raises(extraction.ExtractionRejected):
                gateway.call_tool("corporate_action.notice_for", isin=ABEV, as_of=POISONED_DAY)


class TestTheCrossCheckCannotBeDisabledByOmission:
    """`_cross_check` only runs on the arguments it is given, so an omitted argument silently
    disables it -- measured, omitting both returns withholding 0.00% with no exception. Sourcing
    them inside the tool is what makes the control unconditional."""

    def test_the_domicile_comes_from_the_security_master(self):
        from nav_sentinel.tools import books_and_records as bnr

        assert bnr.security(ABEV).country == "BR"
        with identity.acting_as("corporate-actions-investigator"):
            notice = corporate_action.notice_for(ABEV, CLEAN_DAY)
        assert any("BR treaty" in c for c in notice["corroborated_against"])

    def test_the_gross_rate_comes_from_the_books(self):
        with identity.acting_as("corporate-actions-investigator"):
            notice = corporate_action.notice_for(ABEV, CLEAN_DAY)
        assert any("matches the books" in c for c in notice["corroborated_against"])
        assert corporate_action._books_gross_rate(ABEV, CLEAN_DAY) == Decimal("0.175000")

    def test_the_gross_rate_lookup_is_scoped_to_one_cycle(self):
        """Two dividend movements share the id `CASH-DIV-ABEV` across July and August. Both cycles
        happen to declare the same per-share rate, so comparing the two results proves nothing --
        what matters is that exactly one movement is selected. A lookup that swept both would find
        two candidates and return None."""
        assert corporate_action._books_gross_rate(ABEV, date(2026, 7, 17)) is not None
        assert corporate_action._books_gross_rate(ABEV, CLEAN_DAY) is not None
        assert corporate_action._books_gross_rate(ABEV, date(2026, 6, 30)) is None

    def test_an_ambiguous_cash_match_returns_nothing_rather_than_guessing(self, monkeypatch):
        """A wrong cross-check is worse than an absent one: it would corroborate a figure the books
        never stated."""
        from nav_sentinel.tools import books_and_records as bnr

        movements = bnr.cash_movements("accounting")
        on_the_day = [
            m for m in movements if m.movement_type == "dividend" and m.value_date == CLEAN_DAY
        ]
        assert len(on_the_day) == 1, f"expected one dividend on {CLEAN_DAY}, got {len(on_the_day)}"
        # Duplicating *that* movement is what creates the ambiguity. Duplicating July's would not,
        # which is what an earlier version of this test did -- and it passed for the wrong reason.
        monkeypatch.setattr(
            bnr, "cash_movements", lambda _source: [*movements, on_the_day[0].model_copy()]
        )
        assert corporate_action._books_gross_rate(ABEV, CLEAN_DAY) is None

    def test_a_notice_corroborating_nothing_is_refused(self, monkeypatch):
        monkeypatch.setattr(corporate_action, "_books_gross_rate", lambda *_a: None)
        from nav_sentinel.tools import books_and_records as bnr

        monkeypatch.setattr(bnr, "security", lambda _isin: None)
        with identity.acting_as("corporate-actions-investigator"):
            with pytest.raises(extraction.ExtractionRejected, match="corroborates nothing"):
                corporate_action.notice_for(ABEV, CLEAN_DAY)


class TestTheQuarantineHoldsInsideTheTool:
    def test_the_extractor_runs_with_no_identity_bound(self):
        """`_require_quarantine` refuses to parse while an identity is bound, and `call_tool` runs
        this function inside `acting_as` -- so the binding has to be dropped for the parse."""
        seen: list[str | None] = []
        original = extraction.extract_corporate_action

        def spy(*args, **kwargs):
            seen.append(
                identity.current_or_none().agent_id if identity.current_or_none() else None
            )
            return original(*args, **kwargs)

        import unittest.mock

        with unittest.mock.patch.object(extraction, "extract_corporate_action", spy):
            with identity.acting_as("corporate-actions-investigator"):
                corporate_action.notice_for(ABEV, CLEAN_DAY)
        assert seen == [None], f"the extractor ran while bound to {seen}"

    def test_the_binding_is_restored_after_the_tool_returns(self):
        with identity.acting_as("corporate-actions-investigator"):
            corporate_action.notice_for(ABEV, CLEAN_DAY)
            assert identity.current().agent_id == "corporate-actions-investigator"


class TestTheCassetteCoversWhatTheGoldenNeeds:
    def test_the_notices_run_offline(self):
        assert corporate_action._use_live() is False
        assert corporate_action.CASSETTE.exists()

    def test_every_corporate_action_scenario_in_the_golden_has_a_notice(self):
        """Three of the four had none, so they could only ever have been refused -- a direct hit on
        the root-cause accuracy the eval reports."""
        import yaml

        golden = yaml.safe_load(
            (corporate_action.CASSETTE.parents[2] / "eval" / "golden_breaks.yaml").read_text()
        )
        recorded = set(corporate_action._cassette())
        missing = []
        for cycle in golden if isinstance(golden, list) else [golden]:
            for scenario in cycle.get("scenarios", []):
                if scenario.get("capability") != "nav.corporate_action":
                    continue
                key = f"{scenario['isin']}|{cycle['as_of']}"
                if key not in recorded:
                    missing.append((scenario["scenario"], key))
        assert not missing, f"golden scenarios with no recorded notice: {missing}"

    def test_an_unrecorded_date_says_what_is_recorded(self):
        with identity.acting_as("corporate-actions-investigator"):
            with pytest.raises(corporate_action.NoticeUnavailable, match="Recorded:"):
                corporate_action.notice_for(ABEV, date(2020, 1, 1))

    def test_a_split_is_corroborated_against_the_share_counts(self):
        """A split states no rate and no withholding, so it corroborated nothing and was refused --
        a good notice rejected. Exempting splits was the wrong fix: the ratio is checkable against
        the strongest evidence there is, the books' own share counts."""
        with identity.acting_as("corporate-actions-investigator"):
            notice = corporate_action.notice_for(MSFT, CLEAN_DAY)
        assert any("split ratio 2:1 matches" in c for c in notice["corroborated_against"])

    def test_a_split_ratio_disagreeing_with_the_books_is_not_corroborated(self, monkeypatch):
        from nav_sentinel.tools import books_and_records as bnr

        real = bnr.positions

        def halved(source):
            return [
                p.model_copy(update={"quantity": p.quantity / 3})
                if source == "custodian" and p.isin == MSFT
                else p
                for p in real(source)
            ]

        monkeypatch.setattr(bnr, "positions", halved)
        with identity.acting_as("corporate-actions-investigator"):
            with pytest.raises(extraction.ExtractionRejected, match="corroborates nothing"):
                corporate_action.notice_for(MSFT, CLEAN_DAY)

    def test_the_split_notice_extracts_a_ratio_and_no_rate(self):
        with identity.acting_as("corporate-actions-investigator"):
            notice = corporate_action.notice_for(MSFT, CLEAN_DAY)
        assert notice["action_type"] == "stock_split"
        assert notice["split_ratio"] == "2:1"
        assert notice["gross_rate"] is None

    def test_the_cassette_records_the_real_edgar_paths_it_stands_in_for(self):
        """The documents are authored fixtures -- the poisoned one must carry an injection we
        control, which no real filing does -- so the URIs must say what they represent."""
        recorded = json.loads(corporate_action.CASSETTE.read_text())
        assert "authored fixtures" in recorded["note"]
        for entry in recorded["notices"].values():
            assert entry["source_uri"].startswith("https://www.sec.gov/Archives/edgar/")


class TestTheEvidenceRequirementLandsWithTheTool:
    def test_a_corporate_action_verdict_must_cite_a_filing(self):
        assert packs.evidence_requirement_for("nav.corporate_action") == ("filing",)

    def test_the_notice_tool_can_produce_that_fact(self):
        assert "filing" in packs.catalogue()["corporate_action.notice_for"].facts

    def test_the_requirement_does_not_demand_a_rate_a_split_cannot_supply(self):
        """A split notice states no gross rate, so requiring one would deny every split verdict --
        and a per-capability rule has to hold for every case of that capability."""
        assert "gross_rate" not in packs.evidence_requirement_for("nav.corporate_action")


@pytest.mark.live
class TestTheRealFilterAgainstTheRealNotice:
    """Whether Model Armor actually catches *this* payload, against the live regional endpoint.

    Separated from the wiring tests deliberately. Detection is content-sensitive -- this project
    measured the same injection caught 4 of 4 alone and missed 0 of 8 beside one particular filing
    paragraph -- so a change in the service's behaviour should show up here as a failing live test,
    not as the offline suite silently changing meaning.
    """

    @pytest.fixture(autouse=True)
    def _no_stub(self, monkeypatch):
        """Undo the module-level substitution: this class is the one that wants the real service."""
        monkeypatch.setattr(model_armor, "screen", _REAL_SCREEN)

    def test_the_live_filter_blocks_the_poisoned_notice(self):
        with identity.acting_as("corporate-actions-investigator"):
            with pytest.raises(ContentBlocked) as raised:
                corporate_action.notice_for(ABEV, POISONED_DAY)
        assert raised.value.verdict.verdict == "MATCH_FOUND"
        assert "pi_and_jailbreak" in raised.value.verdict.matched_filters

    def test_the_live_filter_admits_the_clean_notice(self):
        """A filter that blocked everything would pass the test above and be useless."""
        with identity.acting_as("corporate-actions-investigator"):
            notice = corporate_action.notice_for(ABEV, CLEAN_DAY)
        assert notice["withholding_pct"] == Decimal("0.15")
