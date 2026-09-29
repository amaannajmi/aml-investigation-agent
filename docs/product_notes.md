# Product notes: what the evaluation says to build next

Written as a product owner reading the evaluation, not as the engineer who built it.

## What the numbers say

1. **Rules fail on context, not arithmetic.** The baseline scores 100% recall on structuring, pass-through, crypto mules and high-risk jurisdictions, where a numeric rule fits. Even on direct sanctions exposure it reaches only 81%, because abbreviated names (`Boris A. Kren`) score below the fuzzy threshold. It fails completely where the signal lives in text or relationships: transliterated sanctions names, payment references ("rent share", "Airbnb split"), and where the money goes next. That is the gap an LLM agent should close, and the evaluation will show whether it does.
2. **The guardrails carry recall.** Model recall is 81%, system recall is 100%, because every miss was a low-confidence close that routed to a human. The confidence gate is the most important safety feature in the system, and its threshold is a product decision about the trade-off between workload saved and risk accepted.
3. **Auto-close is where the value is, and where the risk is.** Two thirds of benign alerts auto-close. Each point of auto-close rate is analyst time saved; each wrongly auto-closed suspicious alert is a potential regulatory finding. These have to be managed together, never optimised separately.

## What v2 (identity, network, disputes) showed

4. **Who controls the account and who it is connected to matters as much as what the money did.** Adding logins, devices, link analysis and disputes lifted rules recall from 81% to 94% and cleared 75% of the student shared-expense false positives, which v1 escalated every time.
5. **Link strength is a product decision, not a detail.** Treating a shared IP like a shared device would flag every flat-share and every mobile user. Grading links and suppressing super-nodes is what makes the network view usable.
6. **The remaining errors need reading, not counting.** A withdrawn dispute, a device used by a closed account months *before* this customer had it, a fresh mule with a new device and nothing else. Rules count; the LLM agent has to read status and timing. That is the test for `make eval-claude`.

## What v3 (account activity, actions, learning) showed

7. **Account activity catches what nothing else does.** Fresh mules have no network and no complaints yet. The only signal is a new device resetting the password and changing the phone, then money flowing through. That took mule recall to 100%.
8. **The recommendation an ops team needs is an action, not a label.** "Suspicious" is not enough. The team needs to know whether to close, restrict, or ask for documents, and when the SAR goes. Tipping-off turns "ask for documents" into a compliance-sensitive step, so it gets its own guardrail.
9. **Analyst decisions are the cheapest labels there are.** Learning from them cleared the traps the rules fell for. But a model that learns from analysts can also learn to agree with the agent. Held-out scoring, challenger status and human review on disagreement are what make that safe.

## Priorities

| # | Item | Why now | How we will know it worked |
|---|---|---|---|
| 1 | Run the Claude agent on the full set and compare per scenario | The only honest answer to "does the LLM add value over rules?" | Recall on `sanctions_transliteration` and `mule_cash_out` up; clearance on `shared_expenses` up; no regression elsewhere |
| 2 | Transliteration-aware screening (phonetic + transliteration variants + secondary identifiers) | 0% recall on a sanctions scenario is not acceptable in any product | Transliteration recall > 90% at an unchanged false-hit rate |
| 3 | Reviewer override loop | Overrides are free, expert-labelled data | Override reasons categorised weekly; top category becomes the next eval scenario |
| 4 | Confidence calibration report | The auto-close gate only works if 0.9 confidence means about 90% correct | Calibration curve within ±5 points per bucket |
| 5 | Time-aware link analysis (overlapping vs sequential device use) and dispute-status weighting | The two v2 false-positive traps | Second-hand-phone and withdrawn-dispute cases cleared, mule recall unchanged |
| 6 | Prompt-injection tests on free-text fields | Payment references are attacker-controlled | Red-team scenarios added to the eval; zero dispositions changed by injected text |

## What I would not build yet

- **Autonomous SAR filing.** No regulator is ready for it, and the value is in the groundwork, not the final click.
- **More typologies before better evaluation.** Every new typology needs labelled cases first. The eval set is the product's spec.

## Open questions for customers

- What confidence threshold would your MLRO sign off for auto-close, and on which alert types first?
- Would you rather see fewer escalations or fewer auto-closes when the agent is uncertain?
- What evidence would your regulator want to see to accept agent-assisted investigations?
