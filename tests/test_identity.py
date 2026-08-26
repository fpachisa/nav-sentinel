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

    def test_a_role_no_band_accepts_cannot_be_assigned(self):
        """`KNOWN_ROLES` is derived from `BAND_REQUIREMENTS`, so checking one against the other is
        a tautology -- it held even for a table whose only role was `janitor`. The property worth
        asserting is that the roles the bands *do* use are accepted and nothing else is."""
        from nav_sentinel.control_plane.approvals import BAND_REQUIREMENTS

        for allowed, _required in BAND_REQUIREMENTS.values():
            for role in allowed:
                assert {role} <= identity.KNOWN_ROLES, f"{role} signs a band and is unassignable"
        assert "janitor" not in identity.KNOWN_ROLES

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
    """These were substring searches over `inspect.getsource` until a review pointed out that the
    most important assertion in this file passed against a function pinning no audience at all."""

    def test_the_audience_is_pinned_to_this_deployment(self, monkeypatch):
        """Without it, a token minted for *any* Google application authenticates here: the
        signature is valid, the issuer is Google, and the token was never meant for us.

        Checked by capturing what is handed to google-auth, because that is the thing that
        decides. A test that greps for `client_id()` in the source passes on
        `verify_oauth2_token(credential, request, None)`.
        """
        from google.oauth2 import id_token

        captured: dict = {}

        def fake(_credential, _request, audience=None, **_kwargs):
            captured["audience"] = audience
            return {
                "iss": "https://accounts.google.com",
                "email": "a@x.com",
                "email_verified": True,
            }

        monkeypatch.setenv("NAV_OAUTH_CLIENT_ID", "123.apps.googleusercontent.com")
        monkeypatch.setattr(id_token, "verify_oauth2_token", fake)
        identity.verify_google_credential("a-token")

        assert captured["audience"] == "123.apps.googleusercontent.com", (
            f"the audience passed to google-auth was {captured['audience']!r}"
        )

    def test_it_refuses_to_run_at_all_without_a_configured_audience(self, monkeypatch):
        """A refactor to `client_id() or None` would make every Google-signed token authenticate,
        because google-auth skips the audience check when it is None. Empty string happens to fail
        closed today; relying on that is relying on a library's `is not None`."""
        from google.oauth2 import id_token

        monkeypatch.delenv("NAV_OAUTH_CLIENT_ID", raising=False)
        monkeypatch.setattr(
            id_token,
            "verify_oauth2_token",
            lambda *a, **k: {"iss": "https://accounts.google.com", "email": "a@x.com"},
        )
        with pytest.raises(ValueError, match="no OAuth client"):
            identity.verify_google_credential("a-token")

    def test_a_garbage_credential_raises(self, monkeypatch):
        monkeypatch.setenv("NAV_OAUTH_CLIENT_ID", "123.apps.googleusercontent.com")
        # `ValueError` specifically: that is what google-auth raises for a malformed or
        # unverifiable token, and it is what the route catches. A blind `Exception` here would
        # also pass if the call failed for an unrelated reason, such as the module not importing.
        with pytest.raises(ValueError):
            identity.verify_google_credential("not-a-jwt")

    def test_a_token_from_another_issuer_is_refused(self, monkeypatch):
        """Was a substring search that passed on a function mentioning the issuer in a comment."""
        from google.oauth2 import id_token

        monkeypatch.setenv("NAV_OAUTH_CLIENT_ID", "123.apps.googleusercontent.com")
        monkeypatch.setattr(
            id_token,
            "verify_oauth2_token",
            lambda *a, **k: {
                "iss": "https://login.example.com",
                "email": "a@x.com",
                "email_verified": True,
            },
        )
        with pytest.raises(ValueError, match="unexpected issuer"):
            identity.verify_google_credential("a-token")


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


class TestAVerifiedAnalystCanActuallyGetIn:
    """The half nothing exercised: a Google email has to survive the *round trip*.

    Every piece of this worked in isolation. The token verified, `principal_for` attached the right
    role, the route set a correctly signed cookie -- and the next request threw it away, because the
    only code that turned a cookie back into a person looked the subject up in the demo roster, where
    no real email address will ever be. Sign-in redirected to sign-in. Forever.

    The suite was green throughout, because no route test ever set `NAV_OAUTH_CLIENT_ID`: the tests
    all took the roster path, which was the path that worked. The state that would have shown this
    red -- a real deployment, a real email -- was the one state never produced.
    """

    ANALYST = "j.laurent@merian.example.com"

    @pytest.fixture
    def google_deployment(self, monkeypatch):
        monkeypatch.setenv("NAV_OAUTH_CLIENT_ID", "123.apps.googleusercontent.com")
        monkeypatch.setenv("NAV_ANALYSTS", f"{self.ANALYST}:controller")
        monkeypatch.setenv("NAV_SESSION_SECRET", "test-key-not-a-real-one")

    def test_the_cookie_the_sign_in_route_sets_resolves_to_that_analyst(self, google_deployment):
        from nav_sentinel.webapp import session

        # Exactly what `auth_google` does once the token holds up.
        principal = identity.principal_for(
            identity.Verified(email=self.ANALYST, email_verified=True)
        )
        resolved = session.verify(session.sign(principal.subject))

        assert resolved is not None, "a cookie this deployment just issued did not resolve"
        assert resolved.subject == self.ANALYST
        assert resolved.role == "controller"

    def test_signing_in_reaches_the_desk_rather_than_the_sign_in_page_again(
        self, google_deployment, monkeypatch
    ):
        """End to end through the HTTP layer, with only the Google round-trip stubbed."""
        from fastapi.testclient import TestClient

        from nav_sentinel import composition
        from nav_sentinel.server import app
        from nav_sentinel.webapp import session

        monkeypatch.setattr(
            identity,
            "verify_google_credential",
            lambda credential: identity.Verified(email=self.ANALYST, email_verified=True),
        )
        composition.configure()
        # `https`, because the session cookie is marked Secure and Cloud Run serves TLS. Over plain
        # http the client is right to withhold it -- and a test that quietly used http would have
        # been testing a transport the deployment never uses.
        client = TestClient(app, follow_redirects=False, base_url="https://testserver")

        client.post("/app/auth/google", data={"credential": "a-token-google-would-accept"})
        assert client.cookies.get(session.COOKIE), "verifying a good token started no session"

        desk = client.get("/app")
        assert desk.status_code == 200
        # The distinguishing marker: the sign-in page offers Google, the desk does not.
        assert "accounts.google.com" not in desk.text, (
            "signed in successfully and was shown the sign-in page again"
        )
        assert self.ANALYST in desk.text

    def test_the_role_comes_from_the_table_on_every_request_not_from_the_cookie(
        self, google_deployment, monkeypatch
    ):
        """Revocation, and the reason the cookie carries no role.

        A role baked in at sign-in outlives the decision to grant it. Reading the table per request
        means taking someone off it ends their session at the next click.
        """
        from nav_sentinel.webapp import session

        cookie = session.sign(self.ANALYST)
        assert session.verify(cookie).role == "controller"

        monkeypatch.setenv("NAV_ANALYSTS", f"{self.ANALYST}:cio")
        assert session.verify(cookie).role == "cio", "the cookie pinned a stale role"

        monkeypatch.setenv("NAV_ANALYSTS", "someone.else@merian.example.com:controller")
        assert session.verify(cookie) is None, (
            "an analyst removed from the table kept a working session"
        )

    def test_the_demo_roster_is_not_a_back_door_into_a_real_deployment(self, google_deployment):
        """The roster's subjects are published in this repository. Google mode must ignore them.

        Not a theoretical tidy-up: leaving both sources live is how a development convenience
        survives into production, and here it would be four named principals with signing authority
        whose identities are in a public git history.
        """
        from nav_sentinel.webapp import session

        roster_subject = session.ROSTER[0].subject
        assert session.verify(session.sign(roster_subject)) is None, (
            f"{roster_subject} is on the demo roster and was accepted by a Google deployment"
        )

    def test_a_forged_cookie_is_still_refused(self, google_deployment):
        """The table lookup must come *after* the signature check, not instead of it."""
        from nav_sentinel.webapp import session

        assert session.verify(f"{self.ANALYST}|deadbeefdeadbeefdeadbeefdeadbeef") is None
        assert session.verify(self.ANALYST) is None


class TestTheSessionCookieIsProtectedInTransit:
    def test_it_is_secure_and_httponly(self, monkeypatch):
        """Secure so it is never sent in clear; HttpOnly so a script cannot read it.

        This cookie *is* the analyst's signing authority for the length of a session. Readable by
        script, it is an approval identity any injected content could lift.
        """
        from fastapi.testclient import TestClient

        from nav_sentinel import composition
        from nav_sentinel.server import app
        from nav_sentinel.webapp import session

        monkeypatch.setenv("NAV_OAUTH_CLIENT_ID", "123.apps.googleusercontent.com")
        monkeypatch.setenv("NAV_ANALYSTS", "j.laurent@merian.example.com:controller")
        monkeypatch.setattr(
            identity,
            "verify_google_credential",
            lambda credential: identity.Verified(
                email="j.laurent@merian.example.com", email_verified=True
            ),
        )
        composition.configure()
        client = TestClient(app, follow_redirects=False, base_url="https://testserver")
        response = client.post("/app/auth/google", data={"credential": "tok"})

        header = response.headers["set-cookie"]
        assert session.COOKIE in header
        assert "Secure" in header, header
        assert "HttpOnly" in header, header


class TestAMalformedAnalystTableFailsReadinessNotEveryPage:
    """The name is the claim; a review pointed out the class tested only half of it, and the other
    half was false -- a one-character typo 500'd every page for every signed-in analyst."""

    """A configuration error should be reported where configuration errors are looked for.

    The role is resolved per request now, so an unparseable table would otherwise raise inside
    whatever an analyst clicked -- a 500 that reads like a service fault and says nothing about the
    environment variable that caused it.
    """

    def test_readyz_refuses_and_names_the_variable(self, monkeypatch):
        from fastapi.testclient import TestClient

        from nav_sentinel import composition
        from nav_sentinel.server import app

        monkeypatch.setenv("NAV_ANALYSTS", "someone@merian.example.com:hed-of-fund-services")
        composition.configure()
        response = TestClient(app).get("/readyz")

        assert response.status_code == 503
        assert "NAV_ANALYSTS" in response.text

    def test_a_good_table_reports_how_many_people_can_sign(self, monkeypatch):
        from fastapi.testclient import TestClient

        from nav_sentinel import composition
        from nav_sentinel.server import app

        monkeypatch.setenv("NAV_OAUTH_CLIENT_ID", "123.apps.googleusercontent.com")
        monkeypatch.setenv("NAV_ANALYSTS", "a@merian.example.com:controller,b@merian.example.com:cio")
        composition.configure()
        body = TestClient(app).get("/readyz").json()

        assert body["identity"] == "google"
        assert body["signatories"] == 2


class TestReadinessAnswersWhetherAnyoneCanActuallySign:
    """`signatories: 2` was a row count presented as an answer to "can this deployment approve
    anything?". The live deployment held two controllers and no CIO, so five of the seven cases in
    the demo cycle could not be signed by anybody -- and the number reported looked healthy."""

    def _readyz(self, monkeypatch, table):
        from fastapi.testclient import TestClient

        from nav_sentinel import composition
        from nav_sentinel.server import app

        monkeypatch.setenv("NAV_OAUTH_CLIENT_ID", "123.apps.googleusercontent.com")
        monkeypatch.setenv("NAV_ANALYSTS", table)
        composition.configure()
        return TestClient(app).get("/readyz").json()

    def test_a_table_with_no_cio_reports_escalation_as_unsignable(self, monkeypatch):
        body = self._readyz(monkeypatch, "a@x.com:controller,b@x.com:controller")
        assert body["signatories"] == 2
        assert "cio_escalation" in body["unsignable_bands"]

    def test_one_controller_cannot_satisfy_four_eyes_and_readiness_says_so(self, monkeypatch):
        body = self._readyz(monkeypatch, "a@x.com:controller,b@x.com:cio")
        assert body["unsignable_bands"] == []

        alone = self._readyz(monkeypatch, "a@x.com:controller")
        assert "four_eyes" in alone["unsignable_bands"], (
            "one person cannot sign a band needing two distinct signatories"
        )

    def test_an_empty_table_reports_every_band_unsignable_without_refusing_readiness(
        self, monkeypatch
    ):
        body = self._readyz(monkeypatch, "")
        assert body["status"] == "ready"
        assert len(body["unsignable_bands"]) == 4
