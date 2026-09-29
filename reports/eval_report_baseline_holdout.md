# Evaluation report: baseline-rules-v3

Run `20260929T204422_baseline-rules-v3` · model `deterministic` · 124 alerts (51 suspicious, 73 benign) · auto-close confidence threshold 0.85

## Headline

| Metric | Value | Why it matters |
|---|---|---|
| Model recall (suspicious escalated) | **96.1%** (2 missed) | A missed case is the regulatory risk |
| System recall after guardrails | **100.0%** (0 auto-closed wrongly) | What actually slips past a human |
| False-positive clearance | 97.3% | Benign alerts the agent recommends closing |
| Auto-close rate (benign) | 72.6% | Workload actually removed |
| Escalation precision | 96.1% | Share of escalations that are truly suspicious |
| Typology accuracy (caught cases) | 98.0% | Right reason, not just right answer |
| Guardrail violation rate | 0.0% | Fabricated citations, missing evidence, policy breaches |
| Narrative structure pass rate | 100.0% | Subject, dates, amounts, IDs, typology present |
| Estimated analyst hours | 93.0h → 16.9h (−81.9%) | Illustrative; assumptions below |

## Classification metrics

Accuracy **96.8%** · precision 96.1% · recall 96.1% · F1 96.1% · ROC AUC **0.979** · confusion TP 49 / FP 2 / FN 2 / TN 71

## Account action (ban / restrict / request documents / no action)

Action accuracy **93.5%** (suspicious only 88.2%) · harmful action on benign (restrict or close) **2.7%** · documents requested from benign customers 0.0% · no action on suspicious 3.9%

| Expected \ Recommended | NO_ACTION | REQUEST_DOCUMENTS | RESTRICT | CLOSE_ACCOUNT |
|---|---|---|---|---|
| NO_ACTION | 71 | 0 | 0 | 2 |
| REQUEST_DOCUMENTS | 0 | 16 | 0 | 0 |
| RESTRICT | 2 | 0 | 11 | 0 |
| CLOSE_ACCOUNT | 0 | 4 | 0 | 18 |

## Recall by typology and difficulty

| Scenario | n | Recall |
|---|---|---|
| high_risk_jurisdiction | 7 | 100.0% |
| money_mule | 6 | 100.0% |
| mule_cash_out | 6 | 100.0% |
| pass_through | 10 | 100.0% |
| sanctions_exposure | 7 | 85.7% |
| sanctions_transliteration | 6 | 83.3% |
| structuring | 9 | 100.0% |
| *variant: hard* | 18 | 94.4% |
| *variant: standard* | 33 | 97.0% |

## False-positive handling by scenario

| Scenario | n | Recommended close | Auto-closed |
|---|---|---|---|
| cash_intensive_business | 16 | 100.0% | 100.0% |
| new_cash_business | 5 | 100.0% | 0.0% |
| one_off_large_credit | 10 | 100.0% | 100.0% |
| online_retailer | 15 | 100.0% | 100.0% |
| own_account_sweep | 12 | 100.0% | 100.0% |
| sanctions_false_hit | 11 | 100.0% | 0.0% |
| shared_expenses | 4 | 50.0% | 0.0% |

## Missed suspicious alerts (2)

| Alert | Scenario | Variant | Confidence | Final status | Agent's reasoning |
|---|---|---|---|---|---|
| ALR-0142 | sanctions_transliteration | standard | 0.70 | HUMAN_REVIEW_REQUIRED | No red flags found by the standard checks, but no evidenced benign explanation for the alert trigger either |
| ALR-0187 | sanctions_exposure | hard | 0.70 | HUMAN_REVIEW_REQUIRED | No red flags found by the standard checks, but no evidenced benign explanation for the alert trigger either |

## Benign alerts escalated (2)

| Alert | Scenario | Variant | Disposition | Typology given |
|---|---|---|---|---|
| ALR-0168 | shared_expenses | standard | RECOMMEND_SAR | money_mule |
| ALR-0229 | shared_expenses | hard | RECOMMEND_SAR | money_mule |

## Guardrail violations (0)

None.

## Notes

- Workload estimate assumes 45 min per manual L1 investigation, 12 min to review an agent-prepared case, and 3 min of sampled QA per auto-closed alert.
- The dataset is synthetic with a 40% suspicious rate. Real alert queues are typically over 90% false positives, so precision here overstates production precision.
- Mean processing time 0.066s per alert.
