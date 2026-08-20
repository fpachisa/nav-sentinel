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

## 2b. Corrections to this plan after review

Reviewed in a fresh context before any code was written. The use case was upheld; **the
architecture was not**, and two acceptance criteria were unsatisfiable as written. What follows is
the revised plan. The original is in git history — the corrections are more instructive than the
first draft.

Five blockers the first draft would have hit, each verified against the code:

1. **`may_delegate_to` in `registry/models.py` red-lines the repo's own platform claim.**
   `tests/test_transfer_agency.py:288` asserts a bare equality: *no file under `registry/` changed
   since the second process arrived.* Delegation permission moves to `ProcessPack`, which
   `packs.py` has already been amended once for with a recorded reason.
2. **`gateway.delegate` cannot invoke an agent.** `agents` is a process package and
   `tests/test_seam.py:129` forbids any transitive path from the control plane to it —
   `TYPE_CHECKING` included, because `_import_graph` walks that too. The gateway evaluates the
   policy and calls an **injected invoker**, registered by `composition` exactly as
   `discover_for_capability` already is.
3. **`memory/` is forbidden to both layers.** It is in `PROCESS_PACKAGES`
   (`tests/test_seam.py:46`) *and* in `FORBIDDEN_TO_A_PROCESS`
   (`tests/test_transfer_agency.py:48`). So neither the control plane nor a pack may import it.
   Memory reaches a pack the way every other capability does: a `ToolSpec` registered by
   `composition` as a platform tool.
4. **P-007 cannot police a recalled fact as the first draft claimed.** Two errors. `declared_facts`
   (`packs.py:126`) iterates `self.tools` only, so a pack declaring an evidence requirement over a
   platform-tool fact **fails to register at all**. And the refusal I attributed to P-007 is
   actually `contract.resolve_citations` (`agents/contract.py:169`) — P-007
   (`policies.py:317`) compares *fact names*, not resolvability. Criterion 4 would have been a test
   passing for a reason unrelated to its name, which is the defect family every review of this
   build has found.
5. **A case cannot belong to two processes.** `CaseFacts.capability` is one string;
   `packs.process_of` returns one pack; `thresholds_for` returns one threshold set. The first draft
   proposed coordination without naming which invariant gave way.

And three things the first draft got factually wrong about the existing code: `Repository.save_case`
is `set()` (`repository.py:203`) — an overwrite, so **there is no case history** to build a stage
machine on; `ExceptionStatus` has ten members, not nine, and `AWAITING_APPROVAL` appears exactly
once in the whole repo, its own definition; and the claim that S11 "subsumes the S3 fan-out work"
is false — resuming a parked case is sequencing, not fan-out, and does not answer "orchestration
at scale".

## 3. Architecture, revised

**The governing decision, made explicitly:** a remediation case belongs to **one new pack** that
owns the stage capabilities and its own tools. Fund accounting and transfer agency stay isolated
and never learn about each other. Transfer agency's contribution arrives through
`gateway.delegate`, and the sub-agent's observations are recorded against the *remediation*
case_id — which satisfies `resolve_citations`'s cross-case rule by construction. A third pack
orchestrates **through the platform**, not around it.

The pack is named `remediation_office/`, not `compliance/`: `src/nav_sentinel/compliance.py`
already exists (281 lines, the stack-compliance probe behind `make compliance`) and a package of
that name cannot coexist with it.

It measures materiality in **affected investors**, not basis points. Two reasons, and the second
is the better one: `packs.register` refuses two packs declaring thresholds for the same unit and
fund accounting owns `bps` — but also, a regulator's materiality threshold is genuinely not the
fund's own auto-clear band, and conflating them would be a domain error as well as a registration
failure.

### 3.1 Stage machine (`control_plane/casefile.py` + `Repository`)

Transitions explicit and validated; unknown transition raises. A skipped stage is refused and
there is a test that produces that state. `Repository` gains history methods — appended, not
overwritten — because `save_case` overwrites today and a stage machine without history is a
variable that happened to survive.

`stage_history` carries a wall-clock write timestamp per transition. That is not decoration: it is
what makes the compressed-timeline claim demonstrable rather than asserted (§4).

### 3.2 Memory, split into two mechanisms

The first draft put both behind one interface. Verified against the installed ADK 2.7.1, that does
not work: `add_session_to_memory` takes an ADK `Session`, which would drag the agent framework
into a layer deliberately built without it (`agent_surface.py` imports no ADK), and
`search_memory` is a **semantic** query keyed on `user_id`. A materiality decision that must come
out the same way twice cannot rest on fuzzy retrieval.

| Mechanism | Backing | Why |
| --- | --- | --- |
| **Recurrence index** — "this fund has had N pricing errors since 1 July" | deterministic query on `Repository`, single equality filter on `recurrence_key`, client-side date filtering | the decision has to be reproducible, and this repo has been burned twice by Firestore composite-index requirements |
| **Case memory** — narrative carry-over between stages weeks apart | ADK `BaseMemoryService`; `VertexAiMemoryBankService` when an agent engine is configured | this is what the interface is actually for, and `custom_metadata` carries provenance |

Honest claim: *"Memory Bank for cross-session case context; a deterministic recurrence index for
the decision that has to be reproducible."*

Both reach a pack as **platform ToolSpecs**, so recall is a `gateway.call_tool` — which means the
wrapper records an `Observation` against the current case with `source`, `args`, `digest` and
projected facts. Provenance is free and citations resolve by construction. That is the real answer
to the laundering worry, and it is stronger than the one the first draft claimed.

**The hole this creates, named:** recalled content re-entered into a model context is a
memory-poisoning surface, and the track names "tool poisoning" explicitly. Screening is triggered
by `spec.untrusted_output` (`gateway.py:229`), which defaults to `False` — so an injected
instruction that survived into a stored summary would be replayed unscreened. The recall ToolSpec
sets `untrusted_output=True`. Cost: one keyword. Gain: the biggest new hole becomes a demo beat.

**And the guard's real strength, stated:** `investigator.unquoted_evidence` demands the verdict
quote what it cites, and `_appears_in` scans numeric literals. That is strong for rates and dates
and **weak for small integers** — a recalled count of `3` matches almost any sentence. Say so
rather than implying uniform strength.

### 3.3 Delegation (`gateway.delegate` + P-008)

Permission lives on `ProcessPack`, not the manifest (blocker 1). The gateway evaluates P-008,
records a decision naming both agents, and calls an invoker injected by `composition` (blocker 2).
Depth bounded at 1.

**Stated as what it is, not what it looks like.** The README already records that Cloud Run gives
one identity per service, so the deployed container collapses every per-agent account into
`nav-runtime` (defect 7, open), and that in-process memory is not a trust boundary. So the claim
is: *the sub-agent's manifest, not the caller's, decides what it may read, enforced at the
gateway.* Not "the sub-agent runs under its own IAM identity". A judge who reads the README and
then watches the video must not catch the two disagreeing.

### 3.4 Event ingress that resumes a parked case

A second Pub/Sub route. Each event loads the case, validates the transition, advances the stage and
re-parks. The word "webhooks" is not used: these are self-generated fixtures, not deliveries from a
third party.

### 3.5 Two track phrases the first draft ignored

- **Discovery** — the track's *first* focus area, and the first draft had no beat for it. Open the
  remediation case with the compliance capability **unpublished**: the registry refuses to route
  and the case escalates to a human. Publish the manifest, re-run, and the same case routes. This
  is machinery that already exists (`discover.coverage`, `republish`, `invalidate`).
- **PII** — nothing anywhere, and this use case hands it over: the impact report is named
  investors with holdings. Model Armor is inbound-only today. Screen the **outbound** path — what
  enters a model context and what a notification draft contains. "Screening all external email
  with Model Armor" is verbatim in the track's own example.

### 3.6 Orchestration at scale

Not a state machine. A concurrency measurement over work that already exists: N cases in one
delivery, decisions correctly isolated per request. The gateway's `ContextVar` was built for
exactly this and the prior defect is measured in the comment at `gateway.py:52` — eight concurrent
cycles reporting 80, 28, 54, 132, 184, 106, 158 and 210 decisions instead of 28 each. One test,
one number, one sentence.

## 4. The multi-week honesty problem

Unchanged in substance: a committed event timeline replayed as real Pub/Sub messages, business
dates weeks apart, wall-clock compressed.

What the review correctly attacked: **both things the first draft offered as proof are
unfalsifiable by someone watching a video.** A test that restarts the process is a test the judge
does not run; a fixture with distant dates is a file the judge does not read. The hostile reading
is *you replayed seven JSON files through one endpoint in ninety seconds and called it 28 days.*

Two purchases that convert assertion into evidence:

1. **Make one gap real.** Fire event 1, **delete the Cloud Run revision**, redeploy from the
   image, fire event 2. Both revision names and both timestamps into `docs/evidence/`, same
   discipline as `S7a-cloud-run.md`. "Here is the revision id that did not exist when this case was
   created" is not a claim a viewer has to trust.
2. **Show `stage_history` in the Firestore console on camera** — seven writes, seven wall-clock
   timestamps, visibly distinct.

And the compressed-clock sentence goes in the **video narration**, not only the README. A README
caveat beside a video implying a month is still the dishonest version.

## 5. Work breakdown, revised

| # | Work | Hours | Rung |
| --- | --- | --- | --- |
| S11.0 | Extend `ADMITTED_PLATFORM_CHANGES` per file with a reason, in the commit that adds each | 0.5 | **M** |
| S11.1 | Stage machine + `Repository` history methods + transition decisions + `stage_history` | 3.5 | **M** |
| S11.2 | Event route, park/resume, timeline fixture | 3.0 | **M** |
| S11.3 | `gateway.delegate` + P-008 on `ProcessPack` + injected invoker + child spans | 3.0 | **M** |
| S11.4 | Recurrence index as a platform tool; one materiality rule consumes it; recurrence lever on the band | 2.5 | **M** |
| S11.5 | `remediation_office` pack — stage capabilities, agent, prompt, thresholds in investors | 2.0 | **M** |
| S11.6 | Discovery beat: unpublished → refused → published → routes | 0.5 | **M** |
| S11.7 | Outbound PII screening + policy + test | 1.5 | **M** |
| S11.8 | `make remediation` walkthrough | 1.5 | **M** |
| S11.9 | Concurrency measurement (orchestration at scale) | 0.75 | S |
| S11.10 | Case memory via `BaseMemoryService` (narrative carry-over) | 1.5 | S |
| S11.11 | Revision-delete evidence for one timeline gap | 0.75 | S |
| S11.12 | `VertexAiMemoryBankService` + Agent Engine in `bootstrap.sh` | 2.0 | C |
| S11.13 | Figure 6 | 1.5 | C |
| S11.14 | Regulator notification stage | 1.0 | C |

**M: 18.0h. M+S: 21.0h.**

Cut order, corrected: **S11.14, S11.13, S11.12** — Figure 6 before the Vertex backend, because a
new figure is a new `check_diagrams.py` gate plus a re-export for four seconds of screen time,
while S11.12 buys the literal phrase the track names.

Note the real constraint is **review rounds, not hours**. Four of the M rungs land on the most
adversarially tested modules in the repo, and `tests/test_readme_claims.py` pins the README's test
count, so every rung that adds tests needs a README edit or the suite goes red.

## 6. Acceptance criteria, revised

1. A case advances through every stage across separate invocations, **with the process restarted
   between two of them**, and closes correctly.
2. Compensation before approval is refused, and the refusal is a recorded policy decision.
3. The register agent's contribution arrives via `gateway.delegate`, and a test asserts the
   caller's allowlist does **not** widen the sub-agent's.
4. A verdict citing a recalled fact that does not resolve is refused — by
   `contract.resolve_citations`, **named correctly**, with P-007 tested separately for what it
   actually does.
5. The same error on its fourth occurrence reaches a different materiality outcome than on its
   first, from the recurrence index, with both thresholds in the fixture.
6. Delegation depth > 1 is refused.
7. ~~One trace across the case.~~ **Impossible and now corrected.** `audit.case_trace` opens a
   fresh root span per invocation and OTel cannot append to a finished trace, so seven deliveries
   are seven traces. The criterion is: **seven traces joined by `nav.case.id`, each stage span
   carrying an OTel `Link` to the previous stage's span context, persisted with the case.** This
   was the video's headline shot and would have failed at recording time.
8. Recall is screened: a poisoned stored summary is caught on the way out of memory, not replayed.
9. An unpublished capability is refused by the registry; publishing it routes the same case.
10. `make verify` still passes offline with no network and no credentials.
11. The seam holds: `remediation_office` imports no other process's modules, same AST test.

## 7. Risks

| Risk | Mitigation |
| --- | --- |
| Review rounds, not hours, are the constraint. | S11.1–S11.3 deliver the claim and are independently demoable. Everything from S11.9 down is droppable without touching it. |
| The seam tests are actively hostile to exactly these changes. | S11.0 exists for this, and each platform change is admitted with a reason in the commit that makes it. |
| Recurrence lever on the band is unbudgeted in the first draft. | Now explicit in S11.4. `band_for` has no such lever today; only `no_auto_clear`, which floors at `SINGLE_REVIEWER`. |
| Memory as an unevidenced-claim laundry. | Recall goes through `call_tool`, so provenance is structural rather than promised. The weak spot (small integers) is stated, not hidden. |
| Demo credibility on the compressed timeline. | Revision-delete evidence and visible `stage_history`; narration says it out loud. |
| Controls that never ran — the recurring defect family. | Every criterion above names a state that must be produced. Criterion 7 is in the list *because* the first draft's version could not have been produced at all. |
