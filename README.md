# AML Investigation Agent

An agentic AI system that investigates transaction-monitoring alerts the way an L1 AML analyst does, recommends a disposition with a cited evidence trail, and hands the case to a human. It ships with the parts that make agentic AI usable in a regulated setting: **an evaluation harness against labelled ground truth, deterministic guardrails, a full audit trail, and a reviewer workbench.**

The agent recommends. People decide. It cannot close a case, file a SAR, or act on an account.

![Case review](docs/screenshot_case_review.png)

## Why this exists

Alert investigation is still the most manual part of most AML programmes: an analyst pulls the KYC profile, reads 90 days of transactions, screens counterparties, looks for a benign explanation, and writes a narrative. Agentic AI can do much of that groundwork. The hard part is not getting a model to produce an answer; it is **knowing whether the answer is right, proving it to an auditor, and making sure the failures land in front of a human.** This project is built around that problem.

## What it does

For each alert the agent:

1. **Gathers**: the alert, the customer's KYC profile, and transactions for the alert window and the prior 60 days
2. **Screens**: fuzzy-matches the customer and every counterparty against a sanctions list, including near-misses
3. **Analyses**: computes behavioural indicators against the customer's *own* history (cash near the reporting threshold, pass-through of large credits, distinct payers, crypto outflows, high-risk jurisdictions)
4. **Looks for a benign explanation**: own-account transfers, documented sale proceeds, a business model that explains the pattern, a long-standing counterparty
5. **Decides**: `CLOSE_FALSE_POSITIVE`, `ESCALATE_L2` or `RECOMMEND_SAR`, with typology, calibrated confidence and cited transaction IDs
6. **Drafts**: a SAR-style grounds-for-suspicion narrative, or the evidenced benign rationale

Then guardrails route the case:

| Guardrail | What it does |
|---|---|
| Citation grounding | Every transaction ID cited must exist and belong to this customer |
| Amount grounding | Every EUR figure in the narrative must trace to a transaction, a sum of cited transactions, or a tool output |
| Evidence required | An escalation with no cited evidence is a violation |
| Sanctions policy | A strong sanctions match with a matching country can never be closed by the agent |
| Confidence gate | A close below 0.85 confidence goes to a human, not auto-close |
| No autonomous filing | SAR recommendations always wait for MLRO approval |

Any violation routes the case to a human. Every tool call, decision and guardrail outcome is written to an append-only JSONL audit log per alert.

## Two agents, same tools

- **`claude`**: a Claude tool-use agent. It plans its own investigation, chooses which tools to call, and must finish by calling `submit_decision` with a structured recommendation. Read-only, scoped to one alert, fail-safe escalation if it runs out of turns.
- **`baseline`**: a deterministic agent that follows a written L1 procedure. Reproducible, needs no API key, and gives the LLM agent a benchmark to beat.

## Results (baseline agent, 248 alerts)

| Metric | Value |
|---|---|
| Model recall on suspicious alerts | **80.8%** (20 of 104 missed) |
| System recall after guardrails | **100%** (no suspicious alert auto-closed) |
| False-positive clearance | 91.7% |
| Auto-close rate on benign alerts | 66.7% |
| Escalation precision | 87.5% |
| Typology accuracy on caught cases | 90.5% |
| Guardrail violations | 0% |
| Estimated analyst hours | 186h → 35h (illustrative assumptions) |

Where it breaks is the useful part:

| Scenario | Result | Why |
|---|---|---|
| Sanctions names transliterated or abbreviated (`Volkoff Marine Trade` for `Volkov Maritime Trading`) | **0% recall** | Fuzzy scores fall below the 88 threshold; fixed rules never see them |
| Direct sanctions exposure with abbreviated names (`Boris A. Kren`, `Irina M. Dolgova`) | **81% recall** | Same fuzzy-threshold problem on shortened personal names |
| Money mules who cash out to individuals, not crypto | **58% recall** | The rule keys on crypto outflow and a "low income" occupation |
| Students splitting rent and holidays with friends | **0% cleared** (all escalated as mule) | Looks identical to fan-in on the numbers; only the references and payees explain it |
| New cash businesses with no history | cleared, but **0% auto-closed** | No history to compare with, so confidence stays below the gate: correctly sent to a human |

The guardrails are what hold system recall at 100%: every miss was a low-confidence close, so it was routed to a person instead of auto-closed. These are exactly the cases where reading context (payment references, near-miss screening results, who the money goes to) should beat rules, which is the job the LLM agent is there to do. Running `make eval-claude` produces the same report for the Claude agent so the two can be compared scenario by scenario.

Full report: [`reports/eval_report_baseline.md`](reports/eval_report_baseline.md)

## Data

Synthetic, generated by [`src/amlagent/data_gen.py`](src/amlagent/data_gen.py): 248 customers, ~12,000 transactions, 248 alerts across 7 suspicious typologies and 7 benign scenarios, each with a standard and a harder variant. All customers, counterparties and sanctioned parties are fictional. The suspicious rate (42%) is far higher than a real queue (typically over 90% false positives), so precision here overstates production precision. See the [model card](MODEL_CARD.md) for limitations.

## Run it

```bash
pip install -r requirements.txt
make all            # generate data, run tests, evaluate the baseline
make app            # reviewer workbench at http://localhost:8501

export ANTHROPIC_API_KEY=...
make eval-claude    # evaluate the Claude agent (AML_AGENT_MODEL to change model)
```

## Project layout

```
src/amlagent/
  data_gen.py          synthetic alert queue with ground truth
  tools.py             investigation tools (the agent's only view of the data)
  agents/claude_agent.py   Claude tool-use agent
  agents/baseline.py   deterministic L1 procedure
  guardrails.py        grounding, evidence, sanctions policy, confidence gate
  audit.py             append-only per-alert audit log
  evaluate.py          metrics, per-scenario breakdown, miss analysis, report
app.py                 Streamlit reviewer workbench with override capture
tests/                 guardrail and agent-loop tests (scripted fake LLM client)
MODEL_CARD.md          intended use, limitations, failure modes, governance
docs/                  narrative QA rubric, product notes
```

## Governance

See [`MODEL_CARD.md`](MODEL_CARD.md) for intended use, limitations and known failure modes, [`docs/narrative_rubric.md`](docs/narrative_rubric.md) for the human QA rubric used on sampled narratives, and [`docs/product_notes.md`](docs/product_notes.md) for what I would build next and why.
