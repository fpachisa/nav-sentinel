# Narration

Spoken text only, one block per shot. Measured, not estimated: `make narration` renders it with
`say` and times it, because a word count is a proxy for duration that assumes the very thing nobody
knows until a voice has read the script.

## What each shot is for

Five sections, in the order a sceptic asks the questions. The right-hand column is the judging
criterion the shot is answering, because a four-minute video has no room for a beat that is not
paying for itself.

| Shot | Answers | Criterion |
|---|---|---|
| 1 · Why this matters | Is the problem real and expensive? | Utility (40%) |
| 2 · The fleet does the work | Does it act on its own, on what, and at what cost? | Utility (40%) · named tech |
| 3 · Evidence and the human gate | Can I trust it, and who is accountable? | Utility · Architecture (30%) |
| 4 · Weeks, not minutes | Does it hold state across departments and time? | Architecture (30%) |
| 5 · Extensible, on Google Cloud | Does it generalise, and is it really running? | Architecture · Demo (30%) |

Two things are said plainly rather than implied, because the brief asks for them and says not to
bury them: **which agent framework** (Google ADK) and **which Gemini models** (3.7 Flash for
investigation, 3.5 Flash Lite for classification, on Vertex AI). Both land in shot 2.

## Where the four minutes go

Measured end to end, with the shot pauses in the file. Re-measure after any edit — only the first
four minutes are evaluated, so overrunning does not truncate the video, it discards the end of it.

    make narration                    # default rate
    make narration RATE=140           # a slow, deliberate read

---

## 1 · Why this matters — 0:00–0:34

> A fund publishes one number every day: its net asset value. Investors buy and sell at that
> number, so a wrong one means compensating them and explaining yourself to a regulator.
>
> Before it can be published, the fund's own books have to agree with the custodian's. Every
> morning they don't, somewhere, and each difference has to be explained before the deadline.
> Today that is skilled people, working by hand, against a clock.

*On screen: the exception queue. Seven differences, impacts in basis points, the approval each one
needs already assigned.*

---

## 2 · The fleet does the work — 0:34–1:32

> I'm signed in with Google. Seven differences at today's valuation point, found by arithmetic over
> two books — no model, because deciding whether two numbers differ is subtraction.
>
> Now watch. One event, published to Pub/Sub. Nobody is driving this.
>
> Eight agents on Google's Agent Development Kit, running Gemini three-point-seven Flash to
> investigate and three-point-five Flash Lite to classify, on Vertex AI. Each case is classified,
> handed to the specialist authorised for that kind of break, investigated against source data, and
> drafted into a correcting entry.
>
> Three specialists engaged. Seventeen source lookups, every one checked against this fund's
> mandate. Forty controls applied. About a minute, and no analyst touched it.
>
> That run cost cents of model time. By hand it is most of an analyst's morning — and a break
> still unexplained at the deadline becomes a NAV struck on wrong numbers: investor compensation,
> and a filing with the regulator.

*On screen: the terminal `gcloud pubsub topics publish`, hands off the keyboard, then Fleet
activity — counters climbing, one gold arc per row walking left to right, the control log
scrolling. Let the numbers move; this shot is the 40% criterion.*

---

## 3 · Evidence and the human gate — 1:32–2:30

> Open one. The cause, and the European Central Bank rate it read, with a digest — so the
> citation can be checked rather than trusted. The correction balances: two legs, residual
> zero.
>
> I'm a controller. This case is above my authority, so the desk won't offer it: the button reads
> CIO to approve, and it's disabled.
>
> A four-eyes case. I sign. Refused — four eyes means two *different* people. Second account, the
> chief investment officer. Granted.
>
> And now the part that matters. Cleared for posting — and no agent in this system can post it.
> That was checked, not claimed: the gateway was asked to post this entry under an agent's
> identity, carrying the signature, and refused.

*On screen: the case page, then both accounts. Linger on the refusal and on "Cleared for posting".*

---

## 4 · Weeks, not minutes — 2:30–2:56

> A published error is not a one-day job. Fund accounting sizes it. Transfer agency is asked,
> through the gateway and under its own identity, who dealt at the wrong price. A repeat is judged
> more harshly than a first.
>
> Three departments, twenty-eight business days. A payment file arrived before approval: refused,
> and recorded. The case is read back from Firestore on every event, so it outlives the process
> that opened it.

*On screen: the remediation timeline. Business dates down the rail, the refused transition in red.*

---

## 5 · Extensible, and running on Google Cloud — 2:56–3:44

> A second department — a share register, counted in units instead of currency — cost five lines
> and no change to the registry. Any process where the work is slow, the evidence matters, and a
> person must answer for the outcome fits the same seam.
>
> And where no specialist is authorised, a break is refused rather than handed to the closest one.
> Four kinds of break here have nobody, and they go to a person.
>
> This runs on Cloud Run, in us-central1, as its own service account. Sign-in is public; the
> endpoints that do work are not — asking it to run a reconciliation without a session gives four
> oh one. State is in Firestore, and here are the traces, one per delivered event.
>
> The agents never get the authority. They gather evidence, they propose, and a person signs.

*On screen: the Fleet page, then the `.run.app` URL, the 401, Firestore, Cloud Trace. The address
bar has carried the proof since shot 1; this is where it is stated.*

---

## Notes for the recording

**Shot 2 is the one to get right.** It is the heaviest criterion and the only shot that shows
autonomy. Warm the service first (`/readyz` twice) — cold, the first case takes 74 seconds instead
of 24, and the whole run 74 instead of about 60.

**Shot 3's first beat changed** when the desk stopped offering actions it knows will fail. It is a
disabled button now, not a click and a refusal. The four-eyes beat still puts a real server-side
refusal on camera, because a controller *is* eligible there and is turned down on count rather than
on role.

**The cost line is defensible, so do not inflate it.** 68 investigations have run on the deployed
service and the project's entire Gemini spend is under a dollar, which makes a seven-case run
comfortably "cents". Do not put a per-case figure on screen: the honest comparison is not compute
against compute, it is compute against an analyst's morning, and against what a mis-struck NAV
costs in compensation and regulatory filings. That third number is the one that actually wins the
argument, and it is the fund's, not ours.

**Say the numbers you actually see.** Three specialists, seventeen lookups and forty controls are
what a warm run produced; if the take differs, read the take. A figure that does not match the
screen is worse than no figure.

**If the voice reads slowly**, the levers in order: inter-shot pauses from 2.5s to 1.5s buys 5s;
then trim shot 5's second sentence, the one about any process fitting the same seam — the first
half already makes the point with a worked example. Not shot 2, and not shot 3.

Shot 5's opening line, "None of this knows about fund accounting", has already been spent: adding
the economics beat to shot 2 put the slow read 0.8s over, and that sentence was the cheapest thing
in the script.
