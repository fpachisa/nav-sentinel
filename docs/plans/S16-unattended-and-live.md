# S16 — the fleet runs unattended, and you can watch it

## Why

Judging criterion 1 is 40% of the score and reads: *"How much real-world friction does the agent
remove **on its own**? We reward autonomous, high-value action over simple chat — agents that make
decisions and complete tasks with **little to no hand-holding**."*

Today no agent in this system runs without a human clicking. The only unattended entry point is the
Pub/Sub push, and it calls `cycle_runner.run`, which detects, scores and bands — arithmetic. Its own
comment says *"nothing is investigated yet"*. So the honest description of the current submission is
"an analyst drives the agents one case at a time", which is the wording of the criterion inverted.

The second half matters as much. Work that happens invisibly reads as nothing happening. A judge
sees a terminal command and then a populated table, with no sense of the volume of work in between —
seven cases, four root-cause families, dozens of governed tool calls. So the autonomy needs a
display.

## Part A — fan out, so the fleet works cases unattended

The cycle handler detects and then **publishes one message per case**; a second subscription works
each case with the fleet.

Not one handler looping seven cases: seven × ~20s is most of the request budget, one slow case fails
the batch, and a retry re-runs the six that succeeded. Per-case messages give per-case retry, a
per-case dead letter, and parallelism across instances — which is also what "multi-agent
orchestration at scale" actually looks like rather than being asserted.

- `nav-exceptions` → detection (unchanged), then publish `{"case_id": ..., "as_of": ...}` per case.
- New topic `nav-cases` + push subscription → `POST /pubsub/case`, which drives
  `workflow.work_case_events` to completion for one case.
- The handler is idempotent: a case with a verdict for the current proposal is already done, and
  Pub/Sub is at-least-once, so redelivery must not re-bill a second investigation.
- Same OIDC audience verification as the existing push handler. Same DLQ shape.

**Cost, stated because it is real:** an unattended run becomes ~28 Gemini calls instead of 4. The
runbook must say so.

## Part B — the live operations view, `/app/live`

One screen, three bands. Everything on it is read back from Firestore, which is the point: the
display is reading the same durable record an auditor would, not a private stream from a worker.

```
┌──────────────────────────────────────────────────────────────────────────────────────┐
│ FLEET ACTIVITY                              valuation 2026-08-17   ● live            │
├──────────┬──────────┬──────────┬──────────┬──────────┬───────────────────────────────┤
│ CASES    │ AGENT    │ TOOL     │ EVIDENCE │ POLICY   │ REFUSALS                      │
│ 5/7      │ INVOKES  │ CALLS    │ RECORDS  │ DECISIONS│                               │
│          │ 11       │ 34       │ 27       │ 61       │ 3                             │
├──────────┴──────────┴──────────┴──────────┴──────────┴───────────────────────────────┤
│ CASE                        TRIAGE  ROUTE   INVESTIGATE  DRAFT   AGENT               │
│ cash-EUR                      ●       ●          ◐         ○     cash-fees…  ← spins │
│ cash-USD                      ●       ●          ●         ●     cash-fees…          │
│ security-FR0000121014         ●       ●          ●         ●     corporate-actions…  │
│ security-GB00BN7SWP63         ●       ✕          —         —     NO PUBLISHED AGENT   │
│ security-US0378331005         ●       ●          ●         ◐     fx-rates…           │
├──────────────────────────────────────────────────────────────────────────────────────┤
│ GOVERNANCE FEED                                                        newest first  │
│ 09:14:22  ALLOW  P-001  ecb_fx.rate_on            fx-rates-investigator@1.3.0        │
│ 09:14:22  ALLOW  P-006  positions                 fx-rates-investigator@1.3.0        │
│ 09:14:21  DENY   P-002  draft rejected: residual  remediation-agent@1.5.0            │
│ 09:14:19  ALLOW  P-005  screened: sec filing      corporate-actions-investigator…    │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

- **Counters** are the felt volume. Big mono numbers that tick up while nobody touches anything.
- **The case grid** is the work made visible: seven rows advancing through four stages at once,
  which a single case page cannot show. The refused row is deliberately in shot — a fleet that
  routes around its own coverage gap is more convincing than one that appears to handle everything.
- **The governance feed** is what makes it read as a control plane rather than a job runner. Every
  line is a real persisted decision with its policy id.

**Polling, not streaming.** With fan-out the browser is not connected to the worker that is doing
the work — it may be a different instance. Firestore is the shared truth, so the page asks for a
snapshot every second. `/app/live.json` returns it; the page diffs and animates the changes.

## What has to change

| Change | Why |
|---|---|
| `recorded_at` on persisted policy decisions, both backends | A feed cannot order across cases without it, and stage records already carry both dates. An audit record with no write time is the weaker record; this is a gap either way. |
| `Repository.recent_decisions(limit)` | The feed is global, not per case. |
| `workflow.live_snapshot(as_of)` | Counters, per-case stage state, recent decisions, in one read. |
| `/app/live` + `/app/live.json` + poll script | The screen. |
| `POST /pubsub/case`, `nav-cases` topic and subscription in `deploy.sh` | Part A. |
| Runbook + narration | The demo shot, and the honest note about model-call cost. |

## Risks I can see

- **An empty screen is worse than no screen.** If the feed and grid are blank because nothing is
  running, the page must say so plainly rather than look broken.
- **The counters must be real.** A number derived from anything other than the persisted records is
  the exact defect family this project keeps hitting. Every one comes from a store read.
- **Polling every second against Firestore** costs reads. Fine for a demo; the page should stop
  polling once every case is terminal, and say it stopped.
- **`recorded_at` on an append-only record** must not change the content-derived id or the
  immutability comparison — the same mistake as `retrieved_at` on observations, which cost a
  production crash. It is incidental to the evidence and must be excluded from the comparison.

## Not in scope

Resuming a parked remediation case from Pub/Sub. Still honestly open.
