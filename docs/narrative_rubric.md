# Narrative QA rubric

Used by a human reviewer on a weekly sample of agent-drafted narratives (all SAR recommendations, 10% of escalations, 5% of auto-closes). Each criterion is scored 0 (missing or wrong), 1 (partial) or 2 (fully met). A narrative scoring 0 on any **critical** criterion fails regardless of total.

| # | Criterion | Critical | 2 = fully met |
|---|---|---|---|
| 1 | **Factual accuracy** | Yes | Every fact, amount, date and counterparty matches the underlying data. No invented details |
| 2 | **Subject identified** | Yes | Customer name, ID, type and relevant KYC context (occupation, expected activity) stated |
| 3 | **What happened** | | Transactions described with dates, amounts, channels and counterparties; totals correct |
| 4 | **Why it is unusual** | Yes | Explicit comparison with the customer's profile and own history, not just "large amounts" |
| 5 | **Typology** | | Correct typology named and linked to the specific behaviour observed |
| 6 | **Benign explanations considered** | | States which legitimate explanations were checked and why they were ruled out (or, for closures, which one applies and the evidence for it) |
| 7 | **Evidence traceable** | Yes | Transaction IDs cited so a reviewer or regulator can verify every claim |
| 8 | **Proportionate conclusion** | | Disposition follows from the evidence; confidence is neither overstated nor hedged into uselessness |
| 9 | **Clarity** | | Readable by a law enforcement officer with no access to internal systems; no jargon or internal codes unexplained |

**Pass:** total ≥ 14 of 18 and no critical zeros.

Criteria 1, 2 and 7 are partly automated by the guardrails (citation and amount grounding, subject check). Criteria 4, 6 and 8 need human judgement and are where LLM narratives most often fall short: they can describe the activity accurately but under-explain *why it is unusual for this customer*.

Track the pass rate and the most common failed criterion by typology. A falling score on criterion 1 is a stop-ship signal.
