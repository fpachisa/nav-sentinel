# Devpost submission copy

Paste-ready. Field headings match Devpost's form. Every number here is checked against the
repository — if you change the code, re-check them before pasting.

---

## Project name

**NAV Sentinel**

## Tagline (one line, 200 char max)

A governed fleet of fund-accounting agents that investigates NAV reconciliation breaks across
departments and weeks — and cannot post a correction without a human signature.

## Track

**Track C — The Fortified Enterprise Fleet**

## Try it out

- **Live service:** https://nav-sentinel-rwkxhtvoeq-uc.a.run.app/app — sign in with Google.
  (Access is limited to the analysts named in the deployment's roster; the demo video shows the
  full flow.)
- **Readiness, open to anyone:** https://nav-sentinel-rwkxhtvoeq-uc.a.run.app/readyz — names the
  store it is actually writing to, how many processes and agents loaded, and whether anyone can
  sign in.
- **Repository:** *(add the GitHub URL)*

---

## Inspiration

Every fund is reconciled twice a day — once by the fund accountant, once by the custodian. When
the two books disagree, someone has to explain the difference before the NAV can be struck, and
the window is measured in hours. The work is skilled, repetitive and almost entirely manual:
pull the break, guess the cause, chase evidence across a price feed, an FX table, a
corporate-action notice and a trade blotter, write the correcting entry, then find someone
senior enough to approve it.

Two things had to become true before this could be automated, and only one of them was ever an
engineering choice.

**The first is capability, and it is genuinely recent.** Explaining a break means reading a notice
written for a person, reasoning about which of several causes fits, deciding what evidence would
settle it, and producing a balanced journal entry against it. Deterministic automation has been
aimed at this for years: it clears the easy cases and leaves behind exactly the ones that need
judgement. An agent that can read the unstructured source, choose which tools to call, and cite
what it actually used is what changes that — and that is a 2026 capability, not a 2023 one.

**The second is permission.** No fund administrator will let an autonomous process near a NAV
without verified identity, enforced separation of duties, screening of anything ingested from
outside, and an audit trail that survives a regulator asking *why* eighteen months later.

So this builds both: **a fleet that can now do the work, and the control plane that makes it
deployable.**

## Why it pays

Three numbers, in the order they matter.

A break investigated by the fleet costs **cents of model time** and about twenty seconds. The same
break by hand is an analyst chasing a price feed, an FX table, a corporate-action notice and a
trade blotter — a substantial part of a morning, every morning, on a deadline.

And the number that actually decides it: a break still unexplained when the valuation window
closes means the NAV is struck on numbers somebody already knew were wrong. That is investor
compensation, a restatement, and a conversation with a regulator.

**So why is it still manual?** Not cost — the arithmetic above has been obvious for years. It is
manual because explaining a break is *judgement*. You read a corporate-action notice written for
humans, work out that the accounting book recognised a dividend gross while the custodian credited
it net of withholding at the issuer's domicile rate, decide whether the treaty allows a reclaim,
and then know which two accounts that lands in. Rules engines and RPA have been thrown at this for
a decade and they clear the easy ones; the residue is exactly the part that needs reasoning over
evidence nobody has structured.

That capability is new. An agent can now read the unstructured notice, choose which sources to
consult, cite what it actually used, and draft a balanced entry against it. That is the whole
reason this is buildable in 2026 and was not in 2023.

What the governance adds is not the capability — it is permission to use it. A fund administrator
will not let an autonomous process near a NAV without verified identity, enforced separation of
duties and an audit trail that survives being asked *why*. So this builds both halves: the fleet
that can now do the work, and the control plane that makes it deployable.

## What it does

NAV Sentinel runs a fleet of specialist agents over a fund's daily reconciliation, and a control
plane that governs them.

1. **Detects** breaks between the accounting and custodian books with deterministic tolerance
   rules. No model — deciding whether two numbers differ is arithmetic, and a model here would
   add cost and non-determinism to a decision that needs neither.
2. **Triages** each break, computes its NAV impact in basis points, and asks the **Agent
   Registry** which specialist is authorised to investigate that root-cause family.
3. **Investigates** through the specialists the registry will route to — corporate actions, FX
   and rates, settlement — each with its own identity, its own read-only tool allowlist, and
   evidence cited from named sources. Pricing and cash-and-fees are declared capabilities with
   no published investigator, on purpose: a break classified as either escalates to a human
   rather than being handed to whichever agent looked closest. Coverage gaps are a fact about
   any fleet; routing around them loudly is the part worth building.
4. **Drafts** a balanced correcting entry with the evidence chain attached.
5. **Routes for approval by materiality.** Nothing posts autonomously, at any size.

And when a break turns out to be a NAV *error* rather than a same-day difference, it stops being
a one-day job. A misstatement above tolerance opens a case that runs for weeks and crosses
departments: fund accounting restates, transfer agency works out which investors dealt at the
wrong price and what they are owed, compliance decides whether the regulator must be told, and
the case moves through a stage machine that will not let it skip a step. **That multi-week,
multi-department case is the centre of the project**, and it is why the state has to be durable:
the case outlives the process that opened it.

## How I built it

**Agent framework: Google ADK (`google-adk` 2.7.1).** Every agent is an ADK agent with a
declared toolset.

**Model: Gemini on Vertex AI** — `gemini-3.7-flash` for investigation and drafting, and
`gemini-3.5-flash-lite` for triage classification, because every break passes through triage and
cost per call matters there in a way it does not for a once-per-case investigation.

**On Google Cloud:** Cloud Run (the service and the browser desk), Firestore (case files,
governance decisions, approvals — native mode), Pub/Sub with OIDC push authentication and a
dead-letter topic, Vertex AI, Model Armor for screening untrusted ingested text, Cloud Trace via
OTLP, Cloud Build and Artifact Registry. Authentication is a verified Google account; the
service holds no passwords and never sees one.

**The design decision the whole thing rests on:** agents are not trusted to obey their prompts.
Every tool call passes through a policy gateway that can refuse it, and ten policies are
enforced in code — tool allowlist, draft-only authority, no autonomous posting, approval routing
by materiality, untrusted-ingest screening, data scope, evidence corroboration, legal stage
transitions, delegation depth, and whether any published agent is authorised for a capability at
all. A refusal is recorded as a governance decision with the
policy id attached, so the audit trail contains the things the fleet was *stopped* from doing,
not only what it did.

**Approvals are signatures by named people.** Four-eyes requires two *distinct* principals
holding controller or CIO, checked at grant time, against a Google-verified identity. The role
comes from the deployment's analyst table and is looked up on every request, so authentication
grants nothing on its own and removing someone ends their session immediately.

**Extensibility was a design goal, not an afterthought.** The control plane knows nothing about
fund accounting. Processes plug in through a `ProcessPack` seam, and there are three of them —
NAV reconciliation, transfer agency, and the remediation office. A static check enforces the
layering, so a process cannot reach into the platform's internals and no process can import
another. Adding a fourth department means adding a pack, not editing the core.

## Challenges I ran into

**A bug that only existed once deployed.** `/console` returned 500 in Cloud Run and worked
perfectly on my machine: `Invalid database id %28default%29`. I was confidently wrong twice
about the cause before finding it — a patch-level difference in `google-api-core` between my
laptop and the image. The fix was to pin the whole graph: 95 versions in a constraints file.
My first attempt at that file shipped **empty**, which is worse than not having one, because an
empty constraints file constrains nothing while looking like a control.

**Controls that report success in states where they never ran.** This turned out to be the
defect family that mattered. A helper returned "0 investors affected" for a case it had never
actually examined, which closed a material NAV error with nothing paid to anybody. A
governance record reached nothing durable — one trace span and zero persisted decisions for a
28-day case. Most recently: Google sign-in verified the token, checked the audience, attached
the right role, set a correctly signed cookie — and the next request threw it away, so nobody
could ever get in. Every test was green, because every test took the path that worked.

The operating rule that came out of it: **ask of every green signal what state would make it
red, and then produce that state.** Guards are verified by deliberately breaking the production
code and confirming the test fails.

**Opening the service to a browser meant auditing what that opened.** Cloud Run's IAM layer had
been protecting every route by itself, so making the desk reachable would have published an
endpoint that *runs a reconciliation*. Those routes check a session of their own now, and the
deploy script refuses to open ingress unless an OAuth client and an analyst table are both
configured — there is no configuration in which the service is reachable by anyone and
authenticates nobody.

## Accomplishments I'm proud of

- A case that genuinely runs for weeks across three departments, and can be read back in full by
  a fresh process from Firestore — not a session, not a cache.
- The audit trail records refusals, not just actions. You can ask what the fleet was prevented
  from doing.
- 1039 offline tests that run in about eight seconds and need no cloud credentials, plus a
  documented list of the project's **known defects** in the README. The honest ones are the
  useful ones.
- The control plane is genuinely process-agnostic, proven by three processes rather than
  asserted in a design doc.

## What I learned

That the hard part of an enterprise agent fleet is not the agents. It is the boring
institutional machinery around them — identity, separation of duties, durable state, provable
refusals — and that machinery is what decides whether any of it is deployable. I also learned,
repeatedly, that a passing test suite is a claim and not evidence.

## What's next

Pub/Sub ingress that can resume a parked remediation case; OpenTelemetry span links so the seven
traces of a multi-week case join without a correlation id; outbound Model Armor screening;
binding an approval to a specific proposal rather than to the case; and per-agent IAM identities
so the tool allowlist is enforced by the cloud as well as by the gateway.

## Built with

`google-adk` · `gemini` · `vertex-ai` · `cloud-run` · `firestore` · `pub-sub` · `model-armor`
· `cloud-trace` · `opentelemetry` · `cloud-build` · `artifact-registry` · `python` · `fastapi`
· `pydantic` · `oauth`
