# Narration — for the AI voice

**Budget.** Most AI voices read technical prose at 150–160 words per minute. At 150 wpm, 240 seconds
is 600 words *if nothing is silent* — and things must be silent while the fleet runs and while a
Console page loads. So the budget is **530 spoken words plus about 30 seconds of deliberate silence**,
which lands near 3:45 and leaves headroom. Only the first four minutes are evaluated; overrunning
loses the Google Cloud proof at the end, which is the one required element.

**Generate it as one continuous track**, then cut the screen recording to it. Aligning eight separate
clips is fiddly and drifts; one track with known timings does not.

**Written to be spoken.** No policy codes read aloud as letters — the code appears on screen while the
voice says what it means. Numbers are written the way they should be said.

---

## 1 · The problem — 0:00–0:32 · 78 words

> A fund publishes a price for itself every day. Investors deal at that price, so a wrong one means
> compensating them and explaining yourself to a regulator.
>
> Every morning the fund's books disagree with the custodian's somewhere, and someone has to find
> out why before the deadline. I sign in with Google. This is that desk: seven differences today, and
> the column on the right is who is legally required to sign.

*Silence: 3s on the queue.*

---

## 2 · The stack — 0:35–0:55 · 52 words

> Eight agents, built on Google's Agent Development Kit version two, running Gemini three-point-seven
> Flash for reasoning and three-point-five Flash Lite for classification, served by Vertex AI.
>
> None of them is named in the application code. They're discovered from the registry by the
> capability they declare — and five capabilities here resolve to nobody.

*On screen while this is said: the Fleet page, `gemini-3.7-flash` visible per agent.*

---

## 3 · Before any model — 0:55–1:18 · 55 words

> Open one. Before a single model call: quantity agrees, price agrees, market value differs by
> eighty-six thousand euros — and the exchange rate applied differs.
>
> That's arithmetic over two books. A model here would be spending a request to be told what the
> numbers already say.

---

## 4 · The fleet works — 1:18–2:05 · 84 words

> Now the agents.

*Silence: 12–20s while it actually runs. Do not cut this — a visible wait is evidence.*

> Triage classified it, the registry chose the agent authorised for that capability, and that agent
> investigated using only the tools its own manifest allows.
>
> It found the stale rate and cited the European Central Bank data it read, with a digest of the
> response — so the citation can be checked, not trusted. The correction balances: two legs, residual
> zero.
>
> It cannot post this. Nothing in this fleet can.

---

## 5 · Where it says no — 2:05–2:45 · 96 words · **the centre of the video**

> I'm signed in with Google, and this deployment has me down as a controller. Approve.
>
> Refused — a controller can't clear an escalation; only the chief investment officer can. And
> nothing was recorded: an ineligible signature isn't a partial signature.
>
> Now a four-eyes case. Signed. Refused again — four eyes means two *different* people.
>
> Second account, the CIO. Granted.
>
> And now the part that matters. With a valid approval in hand, posting is still refused. No agent
> in this fleet holds posting authority, and the policy that says so is enforced at the gateway, not
> asked of the model. An approval is necessary, and it is not sufficient.

---

## 6 · Multi-week, multi-department — 2:45–3:05 · 71 words

> A published error runs for weeks. Fund accounting quantifies it. Transfer agency is asked, through
> the gateway and under its own identity, who dealt at the wrong price. A repeat is judged more
> harshly than a first.
>
> Four departments, twenty-eight business days. A payment file arrived before approval: refused, and
> recorded. The wall clock is compressed; the business dates are not.

---

## 7 · On Google Cloud — 3:05–3:38 · 74 words · **required**

> This runs on Cloud Run, in us-central1, as its own service account.
>
> Sign-in is public; the endpoints that do work are not. Asking it to run a reconciliation without
> a session: four oh one. And it reports that it's persisting to Firestore rather than to memory,
> because a service holding its audit trail in memory looks identical to a healthy one from outside.
>
> Here are the stage transitions and the policy decisions in Firestore. And here are the traces, one
> per delivered event.

*Fire the traffic before this shot — Cloud Trace indexes with about forty-five seconds of lag.*

---

## 8 · Close — 3:38–3:50 · 48 words

> A second business process — a share register, in units instead of currency — cost five lines and
> no change to the registry.
>
> The agents never get the authority. They gather evidence, they propose, and a person signs.

---

## Check the length before you record

Do not trust a word count written by hand — the first draft of this file claimed 558 words and
measured 581, which with the silences ran to 4:22 and would have cut the required Google Cloud shot.

    awk '/^> /{gsub(/^> /,""); n+=split($0,a," ")} END{print n, "words →", int(n/150*60), "s at 150 wpm"}' \
      docs/submission/narration.md

Add roughly 25 seconds of silence on top for the run-the-fleet wait and page loads. If your voice
runs faster than 155 wpm, put the slack into shot 4's wait rather than adding words — the visible
wait is what proves it isn't a mockup.
