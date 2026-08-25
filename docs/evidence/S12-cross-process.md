# Evidence — the multi-week case survives the process that made it

Recorded 25 August 2026. Project `all-things-agentic-hack-fp`, Firestore native, `us-central1`.

The claim this file exists to make checkable: **a remediation case's state, evidence and governance
record live in Firestore and nowhere else.** Not "a test asserts it" — a second process that never
saw the first one reads the whole thing back.

## What was run

```
# process 1 — walk the case, two live Gemini calls
NAV_REPOSITORY=firestore NAV_APPROVALS=firestore \
  python -m nav_sentinel.remediation_cli --case-id CASE-REM-CONSOLE-DEMO

# process 2 — a fresh interpreter. Nothing from process 1 is in scope.
NAV_REPOSITORY=firestore python -c '<serve /console and read it>'
```

## What process 2 found

| | |
| --- | --- |
| Repository | `FirestoreRepository` |
| Stage transitions | **7** |
| Business dates | `2026-08-18` → `2026-09-15` (**28 days**) |
| Traces | **7** — one per delivered event |
| Persisted policy decisions | **14** — `P-004-APPROVAL-ROUTE` ×7, `P-008-STAGE-TRANSITION` ×7 |
| Observations | **2**, from two different agents |
| Case document | `error_bps=30`, `affected_investors=4`, `recurrence_key=MERID-GEF:nav_error` |

The two observations are the interesting part, because they are the reasoning chain:

| Tool | Recorded by | Facts |
| --- | --- | --- |
| `memory.prior_errors` | `remediation-officer@1.0.0` | `prior_errors=3`, `since=2026-07-01`, `fund_id=MERID-GEF` |
| `register.dealt_on` | `dealing-impact-reporter@1.0.0` | `holders=4`, `units=101250.0000`, `trade_date=2026-08-17` |

Two departments, two identities, one case. The register observation was produced by an agent the
remediation office reached **through the gateway** and cannot read the tools of; the recurrence
observation was produced by the officer, whose allowlist does not include the register. Both are
recorded against the same `case_id`, which is what makes the officer's citation resolvable.

`/console` rendered all five sections from this data: fleet (8 agents), coverage (14 capabilities,
5 unrouted), case (7 stages), reasoning (2 observations with source and digest), governance
(14 decisions).

## Why seven traces and not one

OpenTelemetry cannot append a span to a finished trace. A case worked across seven separate
invocations is therefore seven traces, joined by `nav.case.id`. The plan's original acceptance
criterion asked for one trace across the case; that was impossible, and it was rewritten before any
code was written rather than discovered while filming.

## What this does *not* show

**Wall-clock time is compressed.** The seven events were delivered in about ninety seconds. The
business dates are 28 days apart and every figure — the recurrence window, the dealing date, the
compensation timeline — is computed from those. What is real here is the dependency on persisted
state, not a month of uptime, and the two date columns in the stage table are recorded separately
so the difference is visible rather than implied.

**Both processes ran on this machine.** They shared credentials and a project; they did not share
memory, a process, or an object graph. Deleting the Cloud Run revision between two events and
redeploying from the image would be stronger, and is not done here.

**One delivery per invocation, driven by a CLI loop.** There is no Pub/Sub ingress that resumes a
parked case from an external event yet, so "long-running async execution" is demonstrated as
*persistence across invocations* rather than as an event-driven runtime.
