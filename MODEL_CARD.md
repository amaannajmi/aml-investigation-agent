# Model card: AML Investigation Agent v0.2.0

## Intended use

Decision support for L1 investigation of transaction-monitoring and payment-screening alerts at a bank, payment institution or e-money firm. The agent gathers evidence, recommends a disposition and drafts a narrative for a human analyst.

**Not intended for:** closing alerts or filing SARs without human oversight; customer risk rating; exiting customers; any decision that directly affects a customer without human review.

## System

| Component | Description |
|---|---|
| LLM agent | Claude via tool use (model set by `AML_AGENT_MODEL`). Read-only tools scoped to one alert; must submit a structured decision; fails safe to escalation after 12 turns |
| Baseline agent | Deterministic rules encoding a written L1 procedure; benchmark and fallback. v1 uses transactions only; v2 adds access, link-analysis and dispute rules |
| Link analysis | Accounts sharing device ID, visitor ID, IP, phone, email or bank/card fingerprint, graded strong/medium/weak by identifier type and how many accounts share it; identifiers shared by more than 25 accounts are suppressed |
| Guardrails | Deterministic, model-independent: citation and amount grounding, evidence required for escalation, sanctions no-close policy, confidence gate for auto-close, no autonomous SAR filing |
| Audit | Per-alert JSONL: every tool call (arguments, output digest, latency), the decision, guardrail outcome, reviewer overrides |

## Evaluation data

v0.2 adds identity, login (IP, device ID, visitor ID, location), bank-instrument and dispute data, 138 network accounts, and planted benign noise (privacy VPN users, second-hand devices, withdrawn disputes, shared households and offices, public wi-fi, carrier NAT).

Synthetic alert queue (248 alerts, 104 suspicious) covering structuring, pass-through, money mules (crypto and cash-out), high-risk jurisdictions, and sanctions exposure (direct and transliterated), plus benign scenarios: cash-intensive businesses (established and newly onboarded), documented large credits, own-account sweeps, sanctions look-alikes, e-commerce payers, and shared personal expenses.

## Metrics tracked

Model recall; system recall after guardrails (suspicious alerts not auto-closed); false-positive clearance; auto-close rate; escalation precision; typology accuracy; guardrail violation rate; narrative structure pass rate; per-scenario and per-difficulty breakdowns; itemised misses.

**Recall is the primary metric.** A missed suspicious case is a regulatory failure; an unnecessary escalation is a cost.

## Known limitations and failure modes

- **Synthetic data.** Patterns are cleaner than reality and were designed by the same author as the baseline rules, so baseline results are an optimistic upper bound for rules on this data. Results do not transfer to production without validation on real, labelled alerts.
- **Class balance.** 42% suspicious versus typically under 10% in production. Precision and workload savings will be lower in practice.
- **Fuzzy screening.** Token-based fuzzy matching misses transliterations and abbreviations (0% recall on that scenario for the baseline). Production screening needs transliteration-aware matching and secondary identifiers (date of birth, address, registration number).
- **Context blindness in rules.** The baseline cannot read payment references, so it escalates benign shared-expense activity and misses mules who cash out without crypto.
- **LLM-specific risks.** Hallucinated facts (mitigated by grounding checks), over-reliance on the alert's framing, inconsistency across runs, and prompt injection via free-text fields such as payment references (references are treated as data; a production system should also sanitise and monitor them).
- **Automation bias.** Reviewers may rubber-stamp well-written narratives. Mitigations: sampled QA of auto-closes, mandatory reasons for overrides, and tracking reviewer agreement rates over time.

- **Link analysis false positives.** Shared identifiers do not prove a relationship. Known traps in the data: a second-hand phone previously used by a closed account (strong device link, no relationship), payer disputes later withdrawn, households and offices behind one IP. The v2 rules fall for the first two; they count signals and don't read timing or status.
- **Guilt by association.** A link to a closed account is a reason to look, not a finding. The agent must say what is shared and when, and the reviewer decides.

## Privacy

IP addresses, device IDs, visitor IDs, phone numbers and emails are personal data under GDPR. In production:
- keep access to linked-account details to the investigation purpose
- show linked accounts' status and reason, never their activity (the tools enforce this)
- apply retention limits to login and device data
- record the lawful basis (legal obligation under AML law) in the DPIA

All identifiers in this project come from reserved ranges and fictional names.

## Bias and fairness considerations

Link analysis can disproportionately flag people who share IPs by necessity: students, shared housing, workers on employer or public wi-fi, and mobile users behind carrier NAT. That is why IP links are weak by default and high-cardinality identifiers are suppressed; monitor escalation rates for customers whose only links are IP-based. The agent sees nationality and residence in the KYC profile. Nationality is **not** used as a risk indicator by the baseline, and the LLM prompt does not ask for it. Jurisdiction risk applies to counterparty countries only, using the FATF call-for-action list. Before production use, outcomes should be tested for disparate escalation rates across nationality, age and customer type on real data, and payment references should be reviewed for proxies (names, languages).

## Human oversight

- Every SAR recommendation requires MLRO approval.
- Every escalation goes to an analyst.
- Closes are auto-eligible only at confidence ≥ 0.85 with all guardrails passed, and remain subject to sampled QA.
- Reviewer overrides require a reason and are logged, providing labelled feedback for re-evaluation.

## Monitoring (recommended for production)

Weekly: override rate by disposition and typology, guardrail violation rate, auto-close QA error rate, confidence calibration (predicted versus observed accuracy), drift in alert mix. Any rise in overrides of closes is treated as a potential recall problem and triggers re-evaluation.
