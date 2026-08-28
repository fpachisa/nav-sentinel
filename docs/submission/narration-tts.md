# Narration with style tags — paste into Google / Gemini TTS

Same words as [narration.md](narration.md), with bracketed style tags for a TTS voice. A test
asserts the two files say exactly the same thing once the tags are stripped, so this cannot quietly
drift from the script that gets measured and recorded.

**How to use it.** Paste one shot at a time and keep the audio files separate — you need them
separate anyway to line each one up against its own footage, and one long generation gives you no
way to retime a single shot without redoing all of it. The tags are hints about delivery, not
words: they should never be audible.

**Tone to aim for:** a practitioner explaining a system to people who will look for the holes in
it. Measured, unhurried, no salesmanship. Shot 3 is the only place to lift the energy, and even
there the lift is precision rather than excitement — the system saying no is the product.

---

## Shot 1 · Why this matters

```
[serious] A fund publishes one number every day: its net asset value. [explanation] Investors buy and sell at that number, so a wrong one means compensating them and explaining yourself to a regulator.

[informative] Before it can be published, the fund's own books have to agree with the custodian's. [explanation] Every morning they don't, somewhere, and each difference has to be explained before the deadline.

[serious] It is still done by hand. [explanation] Explaining a break is judgement, not rules — read the notice, work out which of four causes fits, find the evidence. [emphatic] Software could not do that. Now agents can.
```

## Shot 2 · The fleet does the work

```
[informative] Seven differences at today's valuation point, found by arithmetic over two books — [matter-of-fact] no model needed to subtract.

[instruction] Now watch. [neutral] One event, published to Pub/Sub. [emphatic] Nobody is driving this.

[informative] Eight agents on Google's Agent Development Kit, running Gemini three-point-seven Flash to investigate and three-point-five Flash Lite to classify, on Vertex AI. [explanation] Each case is classified, handed to the specialist authorised for it, and investigated against the sources: a rate table, a dividend notice, the trade blotter. Then a balanced correcting entry, evidence cited.

[informative] Three specialists. Seventeen source lookups, each checked against this fund's mandate. Forty controls applied. [emphatic] About a minute, and no analyst touched it.

[matter-of-fact] That cost cents of model time. [explanation] By hand it is most of an analyst's morning — [serious] and a break unexplained at the deadline is a NAV struck on numbers somebody knew were wrong.
```

## Shot 3 · Evidence and the human gate

```
[instruction] Open one. [informative] The cause, and the European Central Bank rate it read, with a digest — [emphatic] so the citation can be checked rather than trusted. [matter-of-fact] The correction balances: two legs, residual zero.

[neutral] I'm a controller. [explanation] This one is above my authority, so the desk won't offer it — the button reads CIO to approve, disabled.

[neutral] A four-eyes case. I sign. [emphatic] Refused — [explanation] four eyes means two different people. [neutral] Second account, the chief investment officer. [approval] Granted.

[serious] And now the part that matters. [informative] Cleared for posting — and no agent in this system can post it. [emphatic] That was checked, not claimed: [explanation] the gateway was asked to post this entry under an agent's identity, carrying the signature, and refused.
```

## Shot 4 · Weeks, not minutes

```
[informative] A published error runs for weeks. Fund accounting sizes it. [explanation] Transfer agency is asked, under its own identity, who dealt at the wrong price. [serious] A repeat is judged more harshly than a first.

[emphatic] Three departments, twenty-eight business days. [serious] A payment file arrived before approval: refused, and recorded. [explanation] The case is read back from Firestore on every event, so it outlives the process that opened it.
```

## Shot 5 · Extensible, and running on Google Cloud

```
[explanation] A second department — a share register, counted in units instead of currency — cost five lines and no change to the registry. [serious] Any slow, evidence-heavy process where a person must answer for the outcome fits the same seam.

[informative] Where no specialist is authorised, a break is refused rather than handed to the closest one: [serious] four kinds of break here have nobody, and they go to a person.

[informative] This runs on Cloud Run in us-central1, as its own service account. [explanation] Sign-in is public; the endpoints that do work are not — a reconciliation without a session gives four oh one. [neutral] State is in Firestore, and here are the traces, one per delivered event.

[serious] The agents never get the authority. [calm] They gather evidence, they propose, and a person signs.
```

---

## If your voice reads slowly

`make narration RATE=140` measures the slow end. At the time of writing the script lands at 195
seconds at a normal pace and 215 at 140 words per minute, against a 240-second cap — so there is
room. If the voice you pick is slower still, take it in this order:

1. **The inter-shot pauses** — 2.5s × 5 is 12.5s. At 1.5s it is 7.5s.
2. **Shot 5's first sentence** — the extensibility point survives without "None of this knows about
   fund accounting."

Not shot 2, which is the heaviest judging criterion and the only one that shows autonomy. Not shot
3, which is where the refusals are.
