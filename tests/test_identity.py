"""Verified identity, and what it does not grant.

The desk originally let you pick a name from a list. An approval here is a signature by a named
principal and four-eyes counts *distinct people*, so an identity anyone can select is a control
anyone can satisfy alone. These tests cover the replacement and, more importantly, the line between
its two halves: signing in proves who you are, and grants nothing.
"""

from __future__ import annotations

import pytest

from nav_sentinel.webapp import identity
from nav_sentinel.webapp.identity import UnknownAnalyst, Verified


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("NAV_ANALYSTS", raising=False)
    monkeypatch.delenv("NAV_OAUTH_CLIENT_ID", raising=False)


class TestAuthenticationIsNotAuthorisation:
    def test_a_verified_stranger_gets_no_role(self, monkeypatch):
        """The line the whole module exists to draw. Google will happily authenticate anybody."""
        monkeypatch.setenv("NAV_ANALYSTS", "known@fund.example:controller")
        with pytest.raises(UnknownAnalyst) as refused:
            identity.principal_for(Verified("stranger@gmail.com", True, "A Stranger"))
        assert "does not grant a role" in str(refused.value)

    def test_a_listed_analyst_gets_exactly_the_listed_role(self, monkeypatch):
        monkeypatch.setenv("NAV_ANALYSTS", "known@fund.example:controller")
        principal = identity.principal_for(Verified("known@fund.example", True))
        assert principal.subject == "known@fund.example"
        assert principal.role == "controller"

    def test_an_unverified_address_is_refused(self, monkeypatch):
        """Google issues tokens for addresses it has not confirmed. An approval trail is a record
        of who signed, and a name nobody verified is worth less than no name."""
        monkeypatch.setenv("NAV_ANALYSTS", "known@fund.example:cio")
        with pytest.raises(UnknownAnalyst, match="not a Google-verified address"):
            identity.principal_for(Verified("known@fund.example", False))

    def test_an_empty_address_is_refused(self, monkeypatch):
        monkeypatch.setenv("NAV_ANALYSTS", "known@fund.example:cio")
        with pytest.raises(UnknownAnalyst):
            identity.principal_for(Verified("", True))

    def test_the_table_is_case_insensitive_on_the_address(self, monkeypatch):
        """Google returns addresses in a case the operator did not necessarily type."""
        monkeypatch.setenv("NAV_ANALYSTS", "Known@Fund.Example:controller")
        assert identity.principal_for(Verified("known@fund.example", True)).role == "controller"


class TestTheRoleTableRefusesNonsense:
    def test_an_unrecognised_role_is_refused_loudly(self, monkeypatch):
        """A role no band recognises would sit in the table looking like an authority while
        satisfying nothing -- an approver who can never approve."""
        monkeypatch.setenv("NAV_ANALYSTS", "a@x.com:auditor")
        with pytest.raises(ValueError, match="no approval band recognises"):
            identity.authorised()

    def test_every_known_role_is_one_some_band_accepts(self):
        from nav_sentinel.control_plane.approvals import BAND_REQUIREMENTS

        for role in identity.KNOWN_ROLES:
            assert any(role in allowed for allowed, _ in BAND_REQUIREMENTS.values())

    @pytest.mark.parametrize("entry", ["", "   ", "no-colon", ":controller", "a@x.com:"])
    def test_malformed_entries_are_skipped_rather_than_guessed(self, entry, monkeypatch):
        monkeypatch.setenv("NAV_ANALYSTS", entry)
        assert identity.authorised() == {}

    def test_several_analysts_parse(self, monkeypatch):
        monkeypatch.setenv("NAV_ANALYSTS", "a@x.com:controller, b@y.com:cio ,c@z.com:reviewer")
        assert identity.authorised() == {
            "a@x.com": "controller", "b@y.com": "cio", "c@z.com": "reviewer"
        }


class TestTheDeploymentModeIsVisible:
    def test_google_is_off_without_a_client_id(self):
        assert identity.uses_google() is False

    def test_google_is_on_with_one(self, monkeypatch):
        monkeypatch.setenv("NAV_OAUTH_CLIENT_ID", "123.apps.googleusercontent.com")
        assert identity.uses_google() is True

    def test_the_signin_page_says_which_mode_it_is_in(self, monkeypatch):
        """A local roster page that looked identical to real sign-in would make a demo
        indistinguishable from the deployed thing."""
        from nav_sentinel.webapp import pages

        local = pages.signin("2026-08-17")
        assert "identities are not verified" in local
        assert "accounts.google.com" not in local

        google = pages.signin_google("2026-08-17", "123.apps.googleusercontent.com")
        assert "accounts.google.com/gsi/client" in google
        assert "123.apps.googleusercontent.com" in google
        assert "grants nothing" in google


class TestTheTokenIsActuallyChecked:
    def test_the_audience_is_pinned_to_this_deployment(self):
        """Without it, a token minted for *any* Google application authenticates here: the
        signature is valid, the issuer is Google, and the token was never meant for us."""
        import inspect

        source = inspect.getsource(identity.verify_google_credential)
        assert "client_id()" in source
        assert "verify_oauth2_token" in source

    def test_a_garbage_credential_raises(self, monkeypatch):
        monkeypatch.setenv("NAV_OAUTH_CLIENT_ID", "123.apps.googleusercontent.com")
        # `ValueError` specifically: that is what google-auth raises for a malformed or
        # unverifiable token, and it is what the route catches. A blind `Exception` here would
        # also pass if the call failed for an unrelated reason, such as the module not importing.
        with pytest.raises(ValueError):
            identity.verify_google_credential("not-a-jwt")

    def test_the_issuer_is_checked_too(self):
        import inspect

        assert "accounts.google.com" in inspect.getsource(identity.verify_google_credential)


class TestARefusedSignInStartsNoSession:
    def test_a_bad_credential_sets_no_cookie(self, monkeypatch):
        from fastapi.testclient import TestClient

        from nav_sentinel import composition
        from nav_sentinel.server import app
        from nav_sentinel.webapp import session

        monkeypatch.setenv("NAV_OAUTH_CLIENT_ID", "123.apps.googleusercontent.com")
        composition.configure()
        client = TestClient(app, follow_redirects=False)
        client.post("/app/auth/google", data={"credential": "not-a-jwt"})
        assert not client.cookies.get(session.COOKIE)
