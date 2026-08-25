# Demo video — 240 seconds

Capped at four minutes; **only the first four are evaluated**, so nothing important goes after
3:50. Public on YouTube, English narration.

Four things the brief requires, and where each is discharged:

| Requirement | Where | Seconds |
| --- | --- | --- |
| Problem and value | Shot 1 | 0:00–0:35 |
| **Gemini model and agent framework, said clearly** | Shot 2, on screen *and* spoken | 0:35–0:55 |
| The agent doing real work — real logs, a record updating | Shots 3–6 | 0:55–2:55 |
| **Proof it runs on Google Cloud** (required) | Shot 7, and the `.run` URL visible in Shot 8 | 2:55–3:35 |

Nothing in this script is a mockup. Every screen is the running system, and the two numbers quoted
on camera are read off the screen rather than from these notes.

---

## Shot 1 · The problem (0:00–0:35)

**On screen:** the exception desk queue, seven rows, band chips coloured.

> A fund publishes a Net Asset Value every day, and it has to be right the first time — investors
> buy and sell at that price. When the fund's books disagree with the custodian's, someone has to
> find out why before the valuation deadline, and prove afterwards that they did.
>
> This is that desk. Seven differences at today's valuation point. The column on the right is not a
> priority — it's who is legally required to sign the correction.

**Why this opens the video:** the value is not "AI finds breaks". It is that every step is
attributable afterwards.

---

## Shot 2 · The stack, said plainly (0:35–0:55)

**On screen:** split — the Fleet page showing `model: gemini-3.7-flash` per agent, and a terminal
running `make registry`.

> Eight agents, built on **Google's Agent Development Kit, version 2.0**, running **Gemini 3.7 Flash**
> for reasoning and **Gemini 3.5 Flash-Lite** for classification, served by **Vertex AI**.
>
> No agent is named anywhere in the application code. They're discovered from the Agent Registry by
> the capability they declare — and five capabilities here resolve to **NONE**, which means nobody is
> published to handle them and the registry refuses to route rather than picking the closest match.

**Say the model and framework names once, slowly, over text that shows them.** Do not bury.

---

## Shot 3 · The numbers before the model (0:55–1:20)

**On screen:** case page for `US0378331005`, the "What the numbers say" panel.

> Open one. Before any model runs: quantity agrees, market value differs by eighty-six thousand,
> local price agrees — and the FX rate applied differs. One-point-one-five-six-seven against
> one-point-one-five-nine-three.
>
> That's arithmetic over two books. Asking a language model to do subtraction would be spending a
> request to be told what the numbers already say.

---

## Shot 4 · The fleet actually working (1:20–2:05)

**On screen:** click **Run the fleet**. Wait on it — do not cut. Then the case page reloaded.

> Now the agents. Triage classifies it, the registry decides which agent is authorised for that
> capability, and *that* agent investigates using only the tools its own manifest allows.

**On screen:** the established cause, the investigator reference, the evidence table with source and
digest, then the proposed journal legs.

> It found the stale rate and it cited the ECB observation it read — with the source and a digest of
> the response. The correction it drafted balances: two legs, same currency, residual zero.
>
> It cannot post this. Nothing in this fleet can.

---

## Shot 5 · Where it says no (2:05–2:40) — **the beat the video exists for**

**On screen:** sign in as the reviewer → **Approve** → the refusal. Then controller one → refusal.
Then controller two → granted, then the red panel.

> I'm signed in as a reviewer. Approve.
>
> Refused — a four-eyes correction may only be signed by a controller or the CIO. Nothing was
> recorded; an ineligible signature isn't a partial signature.
>
> First controller. Refused again — four eyes means two *different* people.
>
> Second controller. Granted.
>
> And now the part that matters: with a valid approval in hand, posting is **still refused**.
> P-003 — no agent in this fleet holds posting authority. An approval is necessary and not
> sufficient.

---

## Shot 6 · Multi-week, multi-department (2:40–2:55)

**On screen:** the Remediation timeline — seven stages, both date columns, the red refusal.

> A published NAV error runs for weeks. Fund accounting quantifies it; transfer agency is asked —
> through the gateway, under its own identity — who dealt at the wrong price; the remediation office
> decides materiality against this fund's recent history. Twenty-eight business days, four
> departments.
>
> A payment file arrived before approval. Refused, and the refusal is recorded.
>
> The wall clock is compressed. The business dates are not, and both are stored.

---

## Shot 7 · It runs on Google Cloud (2:55–3:35) — **required**

**On screen, in this order:**

1. **Cloud Console → Cloud Run → `nav-sentinel`**: the `.run.app` URL, region `us-central1`,
   the live revision, service account `nav-runtime`, the request graph.
2. **Terminal**, two curls against that URL, side by side:
   - no token → **403**
   - with `gcloud auth print-identity-token` → **200** and `/readyz` showing
     `"repository": "FirestoreRepository"`, three processes, eight agents.
3. **Cloud Console → Firestore**: the `nav_stages` and `nav_decisions` collections, documents
   visible with their timestamps.
4. **Cloud Console → Trace**: the per-case traces.

> Cloud Run, us-central1, running as its own service account. Anonymous request: four-oh-three.
> With an identity token: ready, and reporting that it's persisting to Firestore rather than to
> memory.
>
> Here are the stage transitions and the policy decisions in Firestore, and here are the traces.

**Note for filming:** Cloud Trace indexes with roughly 45 seconds of lag. Fire the traffic before
this shot, not during it.

---

## Shot 8 · Extensibility, in one command (3:35–3:50)

**On screen:** `git diff --stat` for the transfer-agency commit, then `make registry`.

> Adding a second business process — a share register reconciled in units rather than currency —
> changed five lines in the composition root and nothing in the registry. Same nine policies, same
> gateway. Its correction uses no model at all, because that one is arithmetic.

---

## What this video deliberately does not claim

Worth having straight before narrating, because a judge who catches an overstatement discounts
everything else:

- **Wall-clock time is compressed** in the remediation timeline. Said out loud in Shot 6.
- **Per-agent IAM is not what enforces the allowlist.** Cloud Run gives one identity per service, so
  the container runs as `nav-runtime`; what refuses a cross-department read is the *manifest*,
  enforced at the gateway. Don't say "each agent has its own service account" over a Cloud Run
  screen showing one.
- **No Pub/Sub ingress resumes a parked remediation case.** Don't imply an event-driven runtime.
- **Model Armor screens inbound only.** Don't claim outbound screening.
- The recurrence count reads whatever the fund's history holds — it climbs as the demo is re-run, so
  say "prior errors", not a figure.
