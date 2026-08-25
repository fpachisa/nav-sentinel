# Evidence — the deployed service, and the bug only deployment found

Recorded 25 August 2026. Revision **`nav-sentinel-00018-42w`**, `us-central1`, project
`all-things-agentic-hack-fp`, running as `nav-runtime@all-things-agentic-hack-fp.iam.gserviceaccount.com`.

Service URL: `https://nav-sentinel-rwkxhtvoeq-uc.a.run.app`

## Two auth layers, both visible

```
anonymous  GET /app     -> 403
authorised GET /app     -> 200
```

The 403 is the deployment refusing an unauthenticated caller before a request reaches any of this
code. Every route below needs `gcloud auth print-identity-token`.

| Route | Status |
| --- | --- |
| `/health` | 200 |
| `/readyz` | 200 |
| `/app` (exception desk) | 200 |
| `/app/fleet` | 200 |
| `/app/remediation` | 200 |
| `/console` (audit view) | 200 |

```json
{"status":"ready","processes":["nav","rem","ta"],"agents":8,"repository":"FirestoreRepository","capabilities":14}
```

`/readyz` names the store deliberately. A deployment that asked for Firestore and got memory looks
identical to a healthy one from outside and would lose its audit trail when the instance scaled
down, so readiness now **refuses** on that mismatch rather than reporting ready.

## The bug that only existed when deployed

The first redeploy served `/app` correctly and returned **500 on every page that ran a Firestore
query**. Document reads worked, so the desk loaded and the audit view did not. A smoke test that
fetches one document would have passed.

```
google.api_core.exceptions.InvalidArgument: 400 Invalid database id %28default%29
```

Cause: the image resolved `google-api-core` **2.35.0** while this project is tested against
**2.34.0**. Nothing was pinned, so pip took whatever was newest at build time — the artefact that
shipped was not the artefact that was tested.

I got the diagnosis wrong twice on the way. First I blamed `google-cloud-firestore` 2.29.0, pinned
it, redeployed, and the error was identical. Then I suspected `GOOGLE_CLOUD_LOCATION=global` and
disproved it by running the same query locally under three values. Only then did comparing the whole
transport stack point at `api-core`.

The fix is `constraints.txt` — the frozen resolution this project is tested against, 95 packages,
applied by the Dockerfile with `pip install -c constraints.txt`. The shipped image now installs
`google-api-core==2.34.0`, confirmed in the build log.

**And the first attempt at that fix shipped empty.** `python -m pip freeze` into a venv with no pip
produced a zero-line file, which constrains nothing while looking like a control. It went out in a
deploy before anyone noticed. There are now tests asserting the file is non-empty, that every package
which has broken this deployment is pinned, that each pin matches the version installed here, and
that the Dockerfile actually references it.

## What this does not show

- **Per-agent IAM is not what enforces the tool allowlist.** Cloud Run gives one identity per
  service, so the container runs as `nav-runtime`; what refuses a cross-department read is the
  agent's *manifest*, enforced at the gateway. Defect 7 remains open.
- **Cloud Trace indexes with roughly 45 seconds of lag.** Traffic has to be fired before a Console
  shot, not during it.
