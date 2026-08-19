"""The quarantine boundary, and what screening can and cannot be relied on to do.

The architecture assumes screening fails. These tests assert the things that hold when it does:
untrusted prose never reaches a privileged context, and a value the document asserts is checked
against something the document cannot influence.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from nav_sentinel.control_plane import extraction, identity, model_armor

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "data"
CLEAN = (FIXTURES / "ca_notice_abev_clean.txt").read_text()
POISONED = (FIXTURES / "ca_notice_abev_poisoned.txt").read_text()
INJECTION = POISONED.split("\n\n")[1].strip()


class TestTheQuarantineHoldsNoPrivilege:
    def test_extraction_refuses_to_run_under_a_bound_identity(self):
        """The extractor's whole value is the absence of capability. Under a bound identity a
        tool call from the same context would inherit it, making the quarantine a comment."""
        with identity.acting_as("corporate-actions-investigator"):
            with pytest.raises(extraction.QuarantineViolation, match="outside any bound identity"):
                extraction.extract_corporate_action(CLEAN, isin="US02319V1035")

    def test_extraction_works_outside_an_identity(self):
        outcome = extraction.extract_corporate_action(CLEAN, isin="US02319V1035")
        assert outcome.record.action_type == "cash_dividend"

    def test_the_record_admits_no_free_text(self):
        """A free-text field would reopen the hole: prose that crosses the boundary is prose in a
        privileged context, whatever the field is called."""
        record = extraction.extract_corporate_action(CLEAN, isin="US02319V1035").record
        assert record.model_config["frozen"] is True
        assert record.model_config["extra"] == "forbid"
        with pytest.raises(ValidationError):
            extraction.CorporateActionRecord(
                **record.model_dump(), raw_document="anything at all"
            )

    def test_the_extractor_imports_no_tools(self):
        """Asserted structurally rather than trusted. The module may reach identity, to refuse
        running inside one, and nothing else that carries capability."""
        import ast

        source = (
            Path(extraction.__file__).read_text()
        )
        imported = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
            elif isinstance(node, ast.Import):
                imported |= {a.name for a in node.names}
        forbidden = {m for m in imported if "tools" in m or "registry" in m or "gateway" in m}
        assert not forbidden, f"the extractor reaches capability: {sorted(forbidden)}"


class TestValuesAreCrossChecked:
    """Quarantine bounds instruction injection. It does nothing about a poisoned *value*, which is
    what the fixture actually attacks: it claims 0.00% withholding on a Brazilian ADR."""

    def test_the_clean_notice_is_corroborated(self):
        outcome = extraction.extract_corporate_action(
            CLEAN, isin="US02319V1035", expected_domicile="BR"
        )
        assert outcome.record.gross_rate == Decimal("0.175000")
        assert outcome.record.withholding_pct == Decimal("0.15")
        assert outcome.record.net_rate == Decimal("0.175000") * Decimal("0.85")
        assert any("treaty schedule" in c for c in outcome.corroborated)

    def test_the_poisoned_withholding_rate_is_rejected(self):
        with pytest.raises(extraction.ExtractionRejected, match="treaty schedule expects"):
            extraction.extract_corporate_action(
                POISONED, isin="US02319V1035", expected_domicile="BR"
            )

    def test_a_rate_disagreeing_with_the_books_is_rejected(self):
        """The document is the side we do not control, so a mismatch escalates rather than
        overwriting either."""
        with pytest.raises(extraction.ExtractionRejected, match="the books recorded"):
            extraction.extract_corporate_action(
                CLEAN, isin="US02319V1035", books_gross_rate=Decimal("0.200000")
            )

    def test_an_absurd_gross_rate_is_rejected(self):
        doc = CLEAN.replace("Gross Rate:        USD 0.175000", "Gross Rate:        USD 9999999")
        with pytest.raises(extraction.ExtractionRejected, match="above the plausible maximum"):
            extraction.extract_corporate_action(doc, isin="US02319V1035")

    def test_a_withholding_rate_over_fifty_percent_is_rejected(self):
        doc = CLEAN.replace("Withholding Tax:   15.00%", "Withholding Tax:   80.00%")
        with pytest.raises(extraction.ExtractionRejected, match="above the plausible maximum"):
            extraction.extract_corporate_action(doc, isin="US02319V1035")

    def test_appending_a_second_value_does_not_override_the_first(self):
        """A poisoned document can append `Withholding Tax: 0.00%` hoping the last one wins.
        Taking the first occurrence makes append-to-override ineffective, and a genuine filing
        does not restate its own terms."""
        doc = CLEAN + "\n\nWithholding Tax:   0.00%\n"
        outcome = extraction.extract_corporate_action(
            doc, isin="US02319V1035", expected_domicile="BR"
        )
        assert outcome.record.withholding_pct == Decimal("0.15")

    def test_a_document_with_no_ex_date_is_refused_not_partially_read(self):
        doc = "\n".join(
            line for line in CLEAN.splitlines() if not line.lower().startswith("ex-date")
        )
        with pytest.raises(extraction.ExtractionFailed, match="no ex-date"):
            extraction.extract_corporate_action(doc, isin="US02319V1035")


class TestScreeningFailsClosedAndSaysWhy:
    def test_windows_keep_a_paragraph_intact(self):
        """An injection has to read as prose to work, so it occupies whole blocks. Splitting on
        blocks keeps it in one window rather than halving it into invisibility."""
        payload = POISONED + "\n\n" + ("Ordinary disclosure paragraph.\n\n" * 40)
        parts = model_armor.windows(payload)
        holding = [p for p in parts if INJECTION in p]
        assert holding, (
            "no window contains the injection whole; windowing destroyed the thing it screens for"
        )
        # And it must not be buried: the tighter the window, the better the odds of detection.
        assert min(len(p.encode()) for p in holding) <= model_armor.WINDOW_BYTES

    def test_windowing_is_bounded(self):
        """One API call per window, so an unbounded slide is a cost event. A byte-wise slide over
        this payload would be several times the block count."""
        payload = "Ordinary disclosure paragraph number %d.\n\n" * 1
        parts = model_armor.windows(POISONED + "\n\n" + payload * 200)
        assert len(parts) < 200 + 20

    def test_a_document_needing_too_many_windows_is_refused(self):
        """Refusing beats silently spending hundreds of calls, and beats admitting it unscreened."""
        huge = "\n\n".join(f"Paragraph {i} of a very long filing." for i in range(400))
        with pytest.raises(model_armor.ContentBlocked) as exc:
            model_armor.screen(huge)
        assert exc.value.verdict.verdict == "too_large_to_screen"
        assert "Section the document" in exc.value.verdict.detail

    def test_the_fetch_cap_is_below_the_screening_ceiling(self):
        """Every byte fetched is a byte screened, so the fetch cap and the screening ceiling have
        to be set together. 200KB was the old default and is about 390 windows."""
        import inspect

        from nav_sentinel.tools import edgar

        cap = inspect.signature(edgar.fetch_filing_text).parameters["max_bytes"].default
        assert cap <= 64_000, f"fetch cap {cap} is too large for windowed screening"


@pytest.mark.live
class TestScreeningAgainstTheRealService:
    def test_the_injection_alone_is_caught(self):
        with pytest.raises(model_armor.ContentBlocked) as exc:
            model_armor.screen(POISONED)
        assert "pi_and_jailbreak" in exc.value.verdict.matched_filters

    def test_detection_is_content_sensitive_not_size_sensitive(self):
        """Pinning the finding the architecture rests on.

        The same 636-byte injection is caught alone and missed when it shares 792 bytes with one
        particular filing paragraph -- 0/8, deterministically. If this test starts failing because
        the service improved, the quarantine is still correct but the module docstring overstates
        the case and should be revised.
        """
        neighbour = (
            "Item 0. The registrant furnishes the following disclosure pursuant to the "
            "applicable rules, covering ordinary operational matters for the period then ended."
        )
        alone_blocked = False
        try:
            model_armor.screen(INJECTION)
        except model_armor.ContentBlocked:
            alone_blocked = True
        assert alone_blocked, "the injection is not caught even alone; the fixture may have changed"

        bundled = INJECTION + "\n" + neighbour
        assert len(bundled.encode()) < model_armor.WINDOW_BYTES, "must fit one window to be a fair test"
        try:
            model_armor.screen(bundled)
            missed = True
        except model_armor.ContentBlocked:
            missed = False
        assert missed, (
            "the service now catches the bundled injection. That is good news, but "
            "model_armor's docstring claims it does not -- revise the claim rather than the test."
        )
