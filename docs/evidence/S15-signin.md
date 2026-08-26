# S15 — verified identity on a browsable deployment

Revision `nav-sentinel-00028-d4g`, `us-central1`, running as `nav-runtime`.
Captured 2026-08-26 03:12 UTC against the live service, with no credentials in the request.

## Readiness answers the question it claims to answer

`signatories` used to be a row count, which reports a healthy-looking 2 for a deployment that
cannot approve five of its seven cases. `unsignable_bands` is the honest form: roles *and* the
distinct-signer count, checked against the deployment's own analyst table.

```
$ curl -s https://nav-sentinel-rwkxhtvoeq-uc.a.run.app/readyz
{
    "status": "ready",
    "processes": [
        "nav",
        "rem",
        "ta"
    ],
    "agents": 8,
    "repository": "FirestoreRepository",
    "capabilities": 14,
    "identity": "google",
    "signatories": 2,
    "unsignable_bands": []
}
```

## Public ingress, and nothing that does work is public

```
$ curl -o /dev/null -w "%{http_code}" https://nav-sentinel-rwkxhtvoeq-uc.a.run.app/app
200
$ curl -o /dev/null -w "%{http_code}" https://nav-sentinel-rwkxhtvoeq-uc.a.run.app/console
401
$ curl -o /dev/null -w "%{http_code}" https://nav-sentinel-rwkxhtvoeq-uc.a.run.app/cycle/2026-08-17
401
$ curl -o /dev/null -w "%{http_code}" https://nav-sentinel-rwkxhtvoeq-uc.a.run.app/selftest
401
```

`/app` serves the Google sign-in page to anyone. `/cycle` runs a reconciliation and `/selftest`
reports internals, so both refuse — which is the point of auditing the routes before opening
ingress rather than after.

## The roster door is closed, not merely ignored

The demo roster's four subjects are published in this repository. In a Google deployment the
route that accepts them is gone, so an anonymous caller cannot obtain a cookie signed with this
deployment's key at all — previously it could, and only `verify` stood between that and a session.

```
$ curl -i -X POST -d 'subject=j.laurent@merian.example' https://nav-sentinel-rwkxhtvoeq-uc.a.run.app/app/signin
HTTP/2 404 
set-cookie headers: 0
```

## What this evidence does not show

A completed Google sign-in, which needs a browser and a consent screen. The round trip is
covered offline in `tests/test_identity.py` — through the HTTP layer, with only the call to
Google stubbed — because it was exactly the seam that broke: every part verified correctly and
the join threw the session away, so no one could sign in and 859 tests were green.
