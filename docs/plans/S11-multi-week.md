# S11 — Multi-week, multi-department coordination

**Status:** plan, awaiting review. Not started.
**Written:** 20 August 2026. Deadline 31 August 2026 17:00 PDT.

## 1. Why this section exists

The track description is specific in ways the build has not yet answered:

> Corporate agent discovery, **multi-agent orchestration at scale**, **long-term state
> persistence**, runtime observability, and security posture enforcement. […] Agent Registry;
> **Agent Runtime (long-running async execution)** + **Memory Bank (persistent cross-session
> context)**; Agent Identity; Agent Gateway; Model Armor; Agent Observability.
>
> Example: a **multi-week** vendor onboarding cycle […] remembering negotiation data via Memory
> Bank […] **coordinating with a logistics sub-agent through Agent Gateway**.

Read against that, five of the six named technologies are built and two of the five focus areas
are not.

### Honest scorecard

| Named technology | State | Evidence |
| --- | --- | --- |
| Agent Registry | **built** | versioned manifests, discovery by capability, refuses to route what nobody publishes |
| Agent Identity | **built** | one service account per manifest, per-agent allowlist enforced at the gateway |
| Agent Gateway | **built** | sole path to data, seven policies, every decision recorded |
| Model Armor | **built** | windowed screening of untrusted ingest, verified live (`MATCH_FOUND` on `pi_and_jailbreak`) |
| Agent Observability | **built** | one Cloud Trace per case, reasoning chain with citations to digested observations |
| **Memory Bank** | **absent** | `src/nav_sentinel/memory/__init__.py` is **0 lines**. `recurrence_key` is computed and put on spans, and *nothing reads it*. The old Figure 1 advertised "Memory · recurrence, cross-cycle" — that was a claim with no code. |
| **Agent Runtime** (long-running async) | **absent** | every case completes inside one request. `ExceptionStatus` defines `AWAITING_APPROVAL` and eight other states; nothing suspends or resumes. |

| Focus area | State |
| --- | --- |
| Corporate agent discovery | **strong** |
| Runtime observability | **strong** |
| Security posture enforcement | **strong** |
| Long-term state persistence | **partial** — Firestore persists cases, observations, decisions and approvals; nothing carries context *across sessions or weeks* |
| Multi-agent orchestration at scale | **weak** — a three-step chain per case (triage → investigate → draft). No agent delegates to another. The two processes are deliberately isolated and never coordinate. |

**Multi-week, multi-department coordination is the gap.** Two processes running in parallel is an
extensibility claim, not a coordination one.

## 2. What to build, and why this use case

The natural multi-week, multi-department process in fund administration is **NAV error
remediation**: what happens after a fund publishes a NAV that turns out to be wrong.

It is regulated (CSSF 24/07 in Luxembourg, comparable FCA/IA guidance in the UK), it runs for
weeks, and it cannot be completed by one department:

| Day | Department | What has to happen | Why it cannot be done by the previous department |
| --- | --- | --- | --- |
| 0 | Fund accounting | detect and quantify the misstatement in bps of NAV | — |
| 0–1 | **Transfer agency** | which investors dealt at the wrong price, and for how many units | fund accounting cannot see the share register |
| 1–2 | **Compliance** | is the error material against the regulatory threshold; is compensation and regulator notification required | materiality thresholds are a compliance rule, not an accounting one; **depends on recurrence** — a fund's fourth pricing error this quarter is not treated as its first |
| 2–5 | Oversight | four-eyes approval of the compensation plan | authority the fleet does not hold |
| 7–28 | Transfer agency | compensation paid per investor; confirmations arrive as external events | the case cannot close until every affected investor is confirmed |
| on breach | Compliance | regulator notification | — |

Why this and not a supply chain: it reuses **both existing process packs and the entire control
plane**, it is credible to a judge who knows the industry, and it is legible to one who does not —
*a fund published a wrong price; who was harmed, by how much, who signs off, and prove it three
weeks later.*

It also makes each named technology load-bearing rather than decorative:

- **Memory Bank** — stage 5 runs weeks after stage 2, in a different session, and must know what
  stage 2 established. And compliance's materiality decision depends on *cross-case* recall: how
  many pricing errors this fund has had this quarter. That is the recurrence signal already
  computed and currently thrown away.
- **Agent Runtime** — the case parks between stages awaiting an external event. That is the
  long-running async requirement, and it is genuine here rather than contrived.
- **Agent Gateway** — the fund-accounting orchestrator *delegates* to the register agent. Under
  the sub-agent's own identity, restricted to its own allowlist, recorded as a decision.
- **Agent Identity** — the delegation is the zero-trust beat: a caller cannot lend its privileges.

## 3. Architecture additions

Four additions. Everything else is reuse.

### 3.1 A case that spans stages (`control_plane/casefile.py`)

Today a case is detected, worked and finished inside one process invocation. A remediation case
needs a persisted stage machine:

```
DETECTED → IMPACT_ASSESSED → MATERIALITY_DETERMINED → AWAITING_APPROVAL
         → APPROVED → COMPENSATION_IN_FLIGHT → CLOSED
                                             ↘ NOTIFIED_REGULATOR
```

Rules, to avoid the defects this build keeps producing:

- Transitions are **explicit and validated**; an unknown transition raises rather than defaults.
- The stage is **persisted**, not held in memory, and the store is append-only for history with a
  current-state document (the existing `Repository` split).
- Every transition records a policy decision, so the audit trail answers *who moved this case,
  when, and on what evidence*.
- A stage cannot be skipped. Compensation before approval must be refused, and there must be a
  test that produces that state.

### 3.2 Memory Bank (`memory/`)

Implement ADK's `BaseMemoryService` (`add_session_to_memory`, `add_memory`, `search_memory`) with
**two backends behind one interface**, mirroring the existing `Repository` pattern:

- `FirestoreMemoryBank` — default. Deterministic, offline-testable, no extra provisioning.
- `VertexAiMemoryBankService` — the real thing, from `google.adk.memory`. It requires an
  `agent_engine_id`, so it needs a Vertex AI Agent Engine provisioned in `bootstrap.sh`. Selected
  by config, exactly like `NAV_APPROVALS=firestore|memory`.

Two distinct kinds of memory, and the distinction matters:

| Kind | Scope | Example | Consumed by |
| --- | --- | --- | --- |
| **Case memory** | one case, across sessions and weeks | "TA reported 41 affected investors holding 2.1m units on day 1" | every later stage |
| **Entity memory** | across cases, keyed by `recurrence_key` | "this fund has had 3 pricing errors since 1 July" | compliance's materiality decision |

The second is the one that earns the name. A memory that only remembers within a case is a
database row; a memory that changes a *decision* because of what happened in earlier cases is
cross-session context. The materiality rule must therefore read entity memory, and there must be
a test that the same error is treated differently on its fourth occurrence — measured, with the
threshold stated in the fixture rather than in prose.

**Guard against the obvious failure:** memory is *recalled evidence*, not truth. A recalled fact
must carry its provenance (which case, which observation, when) and P-007 must still apply — a
verdict cannot cite a memory that does not resolve. Otherwise Memory Bank becomes a laundering
route for unevidenced claims, which is precisely the kind of hole the reviews keep finding.

### 3.3 Delegation through the gateway (`gateway.delegate`)

A new gateway entry point and a new policy:

- **P-008 — delegation.** An agent may request a capability only if its manifest declares it in a
  new `may_delegate_to` list. The sub-agent runs under **its own identity**, with **its own**
  allowlist and data scopes. The caller's privileges are not inherited and cannot be lent.
- The delegation is recorded as a decision naming both agents, and the sub-agent's work hangs off
  the same case trace as a child span, so the reasoning chain is one tree.
- Depth is bounded (1 by default). An agent that can delegate to an agent that can delegate back
  is a loop, and a loop inside a model's tool surface is a runaway bill.

This is the piece that turns "two processes on one control plane" into coordination.

### 3.4 Event ingress that resumes a parked case (`server.py`)

Pub/Sub push already works end to end (S7a, verified on revision `nav-sentinel-00010-9x9`). Add a
second route for **remediation events** — a TA impact report, an approval, a payment confirmation
— each of which loads the case, validates the transition, advances the stage and re-parks. This
subsumes the S3 fan-out work.

## 4. The multi-week honesty problem

**I cannot demo three real weeks, and must not imply otherwise.**

The approach: a committed **event timeline fixture** — day 0, 1, 2, 5, 12, 21, 28 — replayed as
real Pub/Sub messages against the deployed service. Each event is genuinely delivered, the case is
genuinely loaded from Firestore, advanced and written back, and the trace genuinely spans the
sequence. What is compressed is wall-clock, nothing else.

Two things make this honest rather than a mock:

1. The case state between events lives **only** in Firestore. Kill the service between two events
   and the next one still works — and there should be a test that does exactly that, because
   otherwise "persistent" is a claim about a variable that happened to still be in scope.
2. The **business dates in the data are weeks apart**, and every materiality and compensation
   figure is computed from those dates. A payment confirmed on day 21 for a deal dated day −2 is
   arithmetic over a 23-day span whether or not I waited 23 days.

The README and the video must both say this in one plain sentence. An implied month of uptime
would be the worst kind of claim: unfalsifiable by a judge and untrue.

## 5. Work breakdown

Ordered by dependency. Sized in hours; M = must, S = should, C = could.

| # | Work | Hours | Rung |
| --- | --- | --- | --- |
| S11.1 | Casefile stage machine + persistence + transition policy decisions | 3.0 | **M** |
| S11.2 | `BaseMemoryService` interface, `FirestoreMemoryBank`, case memory | 3.0 | **M** |
| S11.3 | Entity memory keyed on `recurrence_key`; compliance materiality consumes it | 2.0 | **M** |
| S11.4 | `gateway.delegate` + P-008 + `may_delegate_to` in manifests + child spans | 3.0 | **M** |
| S11.5 | Compliance process pack (third department) — thresholds, capability, agent, prompt | 2.5 | **M** |
| S11.6 | Event timeline fixture + `/remediation/events` route + park/resume | 3.0 | **M** |
| S11.7 | `make remediation` — the on-camera walkthrough of the 28-day case | 1.5 | **M** |
| S11.8 | Vertex AI Memory Bank backend + Agent Engine in `bootstrap.sh` | 2.0 | S |
| S11.9 | Figure 6 — the multi-week coordination diagram | 1.5 | S |
| S11.10 | Regulator notification stage | 1.0 | C |
| S11.11 | Deploy + live evidence doc, as S7a | 1.5 | S |

**Total M: 18.0h. M+S: 23.0h.**

De-scope ladder, in the order I would spend it: S11.10 first, then S11.8 (the Firestore backend
already satisfies the interface and the claim becomes "Memory Bank interface, our backend" — still
true, just less impressive), then S11.9.

## 6. Acceptance criteria

Each of these is a test or a measured artefact, not a paragraph.

1. A case advances through all seven stages across seven separate invocations, with **the process
   restarted between two of them**, and closes correctly.
2. Compensation before approval is **refused**, and the refusal is a recorded policy decision.
3. The register agent's contribution arrives via `gateway.delegate` under `register-investigator`'s
   own identity — asserted by the recorded decision, and by a test that the caller's allowlist does
   **not** widen the sub-agent's.
4. A verdict citing a recalled memory that does not resolve is **refused** by P-007.
5. The same error on its fourth occurrence for a fund reaches a **different** materiality outcome
   than on its first, from entity memory, with both thresholds in the fixture.
6. Delegation depth > 1 is refused.
7. One Cloud Trace shows the whole case as a tree, including the sub-agent's spans.
8. `make verify` still passes offline with no network and no credentials.
9. The seam holds: the compliance pack imports no other process's modules, asserted by the same
   AST test.

## 7. Risks

| Risk | Mitigation |
| --- | --- |
| Scope. This is the largest section since S1, with 11 days left. | The M rungs are 18h and independently demoable. S11.1–S11.4 alone deliver the claim; S11.5–S11.7 make it filmable. |
| Vertex AI Agent Engine provisioning is unfamiliar and may fight us. | It is rung S, behind an interface the Firestore backend already satisfies. Time-box to 2h and drop it if it resists. |
| Memory becomes an unevidenced-claim laundry. | P-007 applies to recalled facts; provenance is mandatory; there is an acceptance test for the refusal. |
| A third pack invites cross-process coupling. | The existing AST seam test is extended to the new package on day one, not after. |
| Demo credibility on the compressed timeline. | Stated plainly in the README, the video and this plan. The persistence test that restarts the process is the substantive answer. |
| The reviews keep finding controls that never ran. | Every acceptance criterion above names a state that must be *produced*, not merely asserted. |

## 8. What the 240-second video gains

The current shot list proves governance on a single case. This adds the shot the track description
is actually asking for: one case, four departments, twenty-eight days, resumed from Firestore
between events, with one trace covering the whole thing and a sub-agent that could not read the
caller's data even though the caller asked it to.
