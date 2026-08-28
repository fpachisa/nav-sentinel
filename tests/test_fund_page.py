"""The fund, its published number, and the two books that have to agree about it.

The first screen anyone asking "why does this matter?" should see. It exists because shot 1 of the
video spent thirty-seven seconds of narration on a static queue: the words were about a published
NAV and two records disagreeing, and the picture was a list of seven rows.

Everything here is read straight from the sources. No case documents, no model, nothing that has
looked at them yet -- which is the point, because this is the state *before* the fleet exists.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from nav_sentinel import composition
from nav_sentinel.control_plane.approvals import Principal
from nav_sentinel.webapp import pages, workflow

ANALYST = Principal(subject="fpachisa@gmail.com", role="controller")


@pytest.fixture
def overview() -> dict:
    composition.configure()
    return workflow.fund_overview()


class TestBothBooksAreReadAndNeitherIsPrivileged:
    def test_each_book_reports_its_own_number(self, overview):
        books = overview["books"]
        assert books["accounting"]["per_share"] != books["custodian"]["per_share"]
        assert books["accounting"]["net_assets"] > 0
        assert books["custodian"]["net_assets"] > 0

    def test_the_difference_is_the_fund_less_the_custodian(self, overview):
        books = overview["books"]
        assert overview["difference"] == (
            books["accounting"]["net_assets"] - books["custodian"]["net_assets"]
        )

    def test_the_basis_points_are_against_the_independent_record(self, overview):
        """The custodian's NAV is the denominator: measuring an error against the book that
        contains it flatters the number."""
        expected = (
            overview["difference"] / overview["books"]["custodian"]["net_assets"] * 10000
        )
        assert overview["bps"] == expected

    def test_the_page_states_that_neither_book_is_assumed_correct(self, overview):
        html = pages.fund(overview, principal=ANALYST)
        assert "Neither book is assumed correct" in html


class TestTheHoldingsSayWhereTheyDisagree:
    def test_a_position_both_books_agree_on_is_not_marked(self, overview):
        agreed = [h for h in overview["holdings"] if not any(h["differs"].values())]
        assert agreed, "every position differs; the contrast is the point of the table"
        for h in agreed:
            assert h["quantity"]["a"] == h["quantity"]["c"]
            assert h["value"]["a"] == h["value"]["c"]

    def test_a_split_is_marked_even_though_the_value_agrees(self, overview):
        """Quantity halved and price doubled: no money moves, and it is still a stock-record
        failure. A table that only compared value would show this row as clean."""
        splits = [
            h
            for h in overview["holdings"]
            if h["differs"]["quantity"] and not h["differs"]["value"]
        ]
        assert splits, "no quantity-only difference in the fixtures"
        for h in splits:
            assert h["differs"]["price"], "a split moves price too"

    def test_the_count_of_disagreeing_positions_matches_the_rows(self, overview):
        assert overview["disagreeing"] == sum(
            1 for h in overview["holdings"] if any(h["differs"].values())
        )

    def test_differing_figures_are_marked_and_agreeing_ones_are_not(self, overview):
        html = pages.fund(overview, principal=ANALYST)
        assert 'class="r num diff"' in html
        assert 'class="r num same"' in html


class TestItReadsTheSourcesRatherThanTheCases:
    def test_it_works_before_any_cycle_has_run(self):
        """The screen has to render on a store with nothing in it -- that is the state it
        describes."""
        from nav_sentinel.control_plane.repository import InMemoryRepository

        composition.configure()
        previous = composition._repository
        composition._repository = InMemoryRepository()
        try:
            data = workflow.fund_overview()
            assert data["known"]
            assert data["holdings"]
            assert "per share" in pages.fund(data, principal=ANALYST)
        finally:
            composition._repository = previous

    def test_an_unknown_valuation_point_says_so_rather_than_failing(self):
        from datetime import date

        composition.configure()
        data = workflow.fund_overview(date(1999, 1, 1))
        assert data["known"] is False
        assert "No valuation recorded" in pages.fund(data, principal=ANALYST)

    def test_the_page_is_gated(self):
        from fastapi.testclient import TestClient

        from nav_sentinel.server import app

        composition.configure()
        assert "Sign in" in TestClient(app).get("/app/fund").text


class TestTheNumbersOnScreenAreTheNumbersInTheBooks:
    def test_the_published_figures_are_rendered_to_four_places(self, overview):
        html = pages.fund(overview, principal=ANALYST)
        for side in ("accounting", "custodian"):
            rendered = f"{overview['books'][side]['per_share']:,.4f}"
            assert rendered in html, rendered

    def test_the_gap_is_rendered_as_an_absolute_with_its_own_sign(self, overview):
        """A minus sign in front of a figure the narration calls "four and a half million" is
        clearer than a negative number the viewer has to interpret."""
        html = pages.fund(overview, principal=ANALYST)
        assert f"{abs(overview['difference']):,.2f}" in html
        assert f"{abs(overview['bps']):,.0f} basis points" in html

    def test_shares_in_issue_are_the_same_on_both_sides(self, overview):
        """They are, in these fixtures. If they ever are not, the per-share comparison is
        meaningless and this should fail rather than mislead."""
        assert isinstance(overview["shares"], int | Decimal)
        assert overview["shares"] > 0


class TestTheSourceDocumentIsShownAsItWasWritten:
    """A corporate-action notice is a filing meant for a person: a gross rate, withholding at the
    issuer's domicile rate, a depositary ratio, in prose. Reading it is the part of this job that
    resisted automation, so putting it on screen is the difference between claiming an agent
    reasons over unstructured evidence and showing it.
    """

    def _notice(self):
        from types import SimpleNamespace

        return SimpleNamespace(
            observed={"filing": "ca_notice_msft_split.txt", "split_ratio": "2:1"},
            source="sec_edgar",
            source_uri="https://www.sec.gov/Archives/edgar/data/789019/msft-20260817.txt",
            digest="52d8407bdc54e14f0000",
            tool="corporate_action.notice_for",
        )

    def test_the_filing_is_read_from_the_recorded_document(self):
        docs = workflow.source_documents([self._notice()])
        assert len(docs) == 1
        assert "CORPORATE ACTION NOTICE" in docs[0]["text"]
        assert "Split Ratio" in docs[0]["text"]

    def test_an_observation_with_no_filing_contributes_nothing(self):
        from types import SimpleNamespace

        rate = SimpleNamespace(
            observed={"rate": "1.1489"}, source="ECB", source_uri="", digest="d", tool="ecb_fx.rate_on"
        )
        assert workflow.source_documents([rate]) == []

    def test_a_filing_that_is_not_on_disk_is_skipped_rather_than_raising(self):
        from types import SimpleNamespace

        missing = SimpleNamespace(
            observed={"filing": "no_such_notice.txt"}, source="sec_edgar",
            source_uri="", digest="d", tool="corporate_action.notice_for",
        )
        assert workflow.source_documents([missing]) == []

    def test_the_document_is_escaped_before_it_reaches_the_page(self):
        """External content reaching a page inside the firm. The property that makes it worth
        screening before a model reads it makes it worth escaping before a browser does."""
        html = pages._documents_panel(
            [{"filing": "x.txt", "source": "sec_edgar", "source_uri": "u",
              "digest": "d" * 20, "tool": "t",
              "text": "<script>alert('x')</script> & <b>bold</b>"}],
            [],
        )
        assert "<script>alert" not in html
        assert "&lt;script&gt;" in html

    def test_the_screening_line_appears_only_when_a_decision_exists(self):
        document = {"filing": "x.txt", "source": "s", "source_uri": "u", "digest": "d" * 20,
                    "tool": "t", "text": "hello"}
        without = pages._documents_panel([document], [])
        assert "Admitted through the gateway" not in without

        with_decision = pages._documents_panel(
            [document],
            [{"nav.policy.id": "P-005-UNTRUSTED-INGEST",
              "nav.policy.reason": "Model Armor is required"}],
        )
        assert "Admitted through the gateway" in with_decision
        assert "P-005-UNTRUSTED-INGEST" in with_decision

    def test_no_documents_renders_nothing_at_all(self):
        assert pages._documents_panel([], []) == ""

    def test_the_panel_is_styled(self):
        assert ".filing{" in pages.CSS
        assert ".filing-src{" in pages.CSS


class TestTheFundPageShowsWhatExplainingOneTakes:
    """The fund page shows two numbers that disagree. This shows why closing that gap is a
    person's morning: neither book says *why* a position differs, and the answer is in a filing
    written for a human and kept somewhere else.

    It is the input to the job, not evidence that an agent did anything — nothing has investigated
    anything at the point this screen is describing.
    """

    def test_it_picks_a_position_the_books_actually_disagree_about(self, overview):
        document = workflow.explaining_document(overview)
        assert document is not None
        holding = next(h for h in overview["holdings"] if h["isin"] == document["isin"])
        assert any(holding["differs"].values())

    def test_it_reads_the_filing_rather_than_calling_the_governed_tool(self):
        """`corporate_action.notice_for` binds an identity, screens the content and writes a policy
        decision. Rendering a read-only page must not leave a governance record claiming an
        investigation happened."""
        import inspect

        # The *call*, not the name: the docstring names the tool in order to explain why it is
        # not called, and a bare substring check fails on the explanation.
        source = inspect.getsource(workflow.explaining_document)
        assert "notice_for(" not in source
        assert "_cassette" in source

    def test_rendering_the_page_records_no_policy_decision(self, overview):
        composition.configure()
        store = composition.store()
        before = len(store.recent_decisions(500))
        overview["explaining"] = workflow.explaining_document(overview)
        pages.fund(overview, principal=ANALYST)
        assert len(store.recent_decisions(500)) == before

    def test_the_panel_states_the_two_quantities_and_that_the_value_agrees(self, overview):
        overview["explaining"] = workflow.explaining_document(overview)
        html = pages.fund(overview, principal=ANALYST)
        assert "96,000" in html and "192,000" in html
        assert "agree on its value to the penny" in html
        assert "CORPORATE ACTION NOTICE" in html

    def test_it_says_this_is_one_of_seven(self, overview):
        """The point is the volume, not the single document."""
        overview["explaining"] = workflow.explaining_document(overview)
        html = pages.fund(overview, principal=ANALYST)
        assert "seven this morning" in html

    def test_no_matching_filing_renders_nothing_rather_than_an_empty_frame(self):
        assert pages._explaining_panel(None) == ""

    def test_an_unknown_valuation_point_has_no_document(self):
        assert workflow.explaining_document({"known": False}) is None
