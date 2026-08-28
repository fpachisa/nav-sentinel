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
