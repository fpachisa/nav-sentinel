"""The application's routes. One GET renders a page; one POST does one thing and redirects.

Post-redirect-get throughout, so a browser refresh never re-runs an approval or re-triggers a model.
That is not a style preference: a refresh that re-submitted a signature would silently add signatures
to a four-eyes case, which is the one thing this application exists to count correctly.

Every write goes through `webapp.workflow`, which goes through the same authority and the same
gateway the CLI uses. The web layer holds no privilege of its own; what it adds is a named analyst,
because four-eyes has to count people and a service token carries none.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from nav_sentinel import composition
from nav_sentinel.control_plane.approvals import Principal
from nav_sentinel.webapp import identity, pages, session, workflow

logger = logging.getLogger("nav_sentinel.webapp")

router = APIRouter()

AS_OF = workflow.DEFAULT_AS_OF


def _who(request: Request) -> Principal | None:
    return session.verify(request.cookies.get(session.COOKIE))


def _signin_page() -> str:
    """Google sign-in when this deployment has a client id; the roster otherwise, labelled."""
    if identity.uses_google():
        return pages.signin_google(AS_OF.isoformat(), identity.client_id())
    return pages.signin(AS_OF.isoformat())


def _to(path: str) -> RedirectResponse:
    # 303, not 302: the browser must follow a POST with a GET, or a refresh re-posts.
    return RedirectResponse(path, status_code=303)


@router.get("/app", response_class=HTMLResponse)
def desk(request: Request) -> str:
    composition.configure()
    principal = _who(request)
    if principal is None:
        return _signin_page()
    return pages.queue(
        workflow.queue(AS_OF), principal=principal, as_of=AS_OF.isoformat()
    )


@router.post("/app/signin")
def signin(subject: str = Form(...)) -> RedirectResponse:
    """Accept a subject only if it is on the roster. Anything else signs nobody in.

    Checked against the roster rather than trusted from the form: a posted subject is caller-supplied
    text, and an application about identity must not mint one from it.
    """
    if identity.uses_google():
        # The roster door is not merely ignored by `verify` in a Google deployment -- it is closed.
        # Leaving it open let an unauthenticated caller on public ingress obtain a cookie signed
        # with this deployment's key, harmless only for as long as `verify` keeps refusing it. One
        # roster-shaped address in `NAV_ANALYSTS` would have turned that into a real session, and
        # the safety of a public endpoint should not rest on a single `if` somewhere else.
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    known = next((p for p in session.ROSTER if p.subject == subject), None)
    response = _to("/app")
    if known is not None:
        response.set_cookie(
            session.COOKIE, session.sign(known.subject), httponly=True, samesite="lax"
        )
    return response


@router.post("/app/auth/google")
def auth_google(credential: str = Form(...)) -> RedirectResponse:
    """Verify a Google ID token and start a session, or refuse without one.

    A refusal sets no cookie and says nothing about *why* on the page beyond "not authorised" --
    telling an unauthenticated caller whether an address is on the analyst list would turn the
    sign-in screen into a directory of who can approve this fund's corrections.
    """
    response = _to("/app")
    try:
        verified = identity.verify_google_credential(credential)
    except ValueError as bad_token:
        logger.warning("outcome=signin_refused reason=%s", type(bad_token).__name__)
        return response
    try:
        principal = identity.principal_for(verified)
    except identity.UnknownAnalyst as refused:
        logger.warning("outcome=signin_refused reason=%s", type(refused).__name__)
        return response
    except ValueError as misconfigured:
        # Distinct from a refusal, and it must be: this branch means the *deployment* is wrong, not
        # the person. Folding the two together reported a broken environment variable to the
        # operator as an authorisation decision, and threw away the message naming the bad role.
        logger.error("outcome=analyst_table_unusable detail=%s", misconfigured)
        return response
    response.set_cookie(
        session.COOKIE, session.sign(principal.subject), httponly=True, samesite="lax", secure=True
    )
    return response


@router.post("/app/signout")
def signout() -> RedirectResponse:
    response = _to("/app")
    response.delete_cookie(session.COOKIE)
    return response


@router.post("/app/cycle")
def run_cycle(request: Request) -> RedirectResponse:
    composition.configure()
    if _who(request) is not None:
        workflow.run_cycle(AS_OF)
    return _to("/app")


@router.get("/app/case/{case_id}", response_class=HTMLResponse)
def case(case_id: str, request: Request) -> str:
    composition.configure()
    principal = _who(request)
    if principal is None:
        return _signin_page()
    return pages.case(workflow.case_detail(case_id, AS_OF), principal=principal)


@router.post("/app/case/{case_id}/work")
def work(case_id: str, request: Request) -> RedirectResponse:
    composition.configure()
    if _who(request) is not None:
        workflow.work_case(case_id, AS_OF)
    return _to(f"/app/case/{case_id}")


@router.post("/app/case/{case_id}/approve")
def approve(case_id: str, request: Request) -> RedirectResponse:
    composition.configure()
    principal = _who(request)
    if principal is not None:
        outcome = workflow.approve(case_id, principal, as_of=AS_OF)
        # Kept on the case so the redirect can show what happened. The alternative -- rendering the
        # result directly from the POST -- makes a refresh re-submit the signature.
        store = composition.store()
        document = dict(store.load_case(case_id) or {})
        document["last_outcome"] = {
            "granted": outcome.granted,
            "message": outcome.message,
            "posting_refused": outcome.posting_refused,
        }
        store.save_case(case_id, document)
    return _to(f"/app/case/{case_id}")


@router.get("/app/fleet", response_class=HTMLResponse)
def fleet(request: Request) -> str:
    composition.configure()
    principal = _who(request)
    if principal is None:
        return _signin_page()
    return pages.fleet(principal=principal)


@router.get("/app/remediation", response_class=HTMLResponse)
def remediation(request: Request, case_id: str = "") -> str:
    composition.configure()
    principal = _who(request)
    if principal is None:
        return _signin_page()
    return pages.remediation(
        composition.store(), case_id or _default_remediation_case(), principal=principal
    )


def _default_remediation_case() -> str:
    import json
    from pathlib import Path

    fixture = (
        Path(__file__).resolve().parents[3] / "fixtures" / "data" / "remediation_timeline.json"
    )
    try:
        return str(json.loads(fixture.read_text())["case_id"])
    except (OSError, KeyError, json.JSONDecodeError):
        return ""
