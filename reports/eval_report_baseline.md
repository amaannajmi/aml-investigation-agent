# Evaluation report: baseline-rules-v3

Run `20260929T204147_baseline-rules-v3` · model `deterministic` · 248 alerts (104 suspicious, 144 benign) · auto-close confidence threshold 0.85

## Headline

| Metric | Value | Why it matters |
|---|---|---|
| Model recall (suspicious escalated) | **96.2%** (4 missed) | A missed case is the regulatory risk |
| System recall after guardrails | **100.0%** (0 auto-closed wrongly) | What actually slips past a human |
| False-positive clearance | 96.5% | Benign alerts the agent recommends closing |
| Auto-close rate (benign) | 65.3% | Workload actually removed |
| Escalation precision | 95.2% | Share of escalations that are truly suspicious |
| Typology accuracy (caught cases) | 98.0% | Right reason, not just right answer |
| Guardrail violation rate | 0.0% | Fabricated citations, missing evidence, policy breaches |
| Narrative structure pass rate | 98.1% | Subject, dates, amounts, IDs, typology present |
| Estimated analyst hours | 186.0h → 35.5h (−80.9%) | Illustrative; assumptions below |

## Classification metrics

Accuracy **96.4%** · precision 95.2% · recall 96.2% · F1 95.7% · ROC AUC **0.98** · confusion TP 100 / FP 5 / FN 4 / TN 139

## Account action (ban / restrict / request documents / no action)

Action accuracy **91.1%** (suspicious only 83.7%) · harmful action on benign (restrict or close) **2.1%** · documents requested from benign customers 1.4% · no action on suspicious 3.8%

| Expected \ Recommended | NO_ACTION | REQUEST_DOCUMENTS | RESTRICT | CLOSE_ACCOUNT |
|---|---|---|---|---|
| NO_ACTION | 139 | 2 | 0 | 3 |
| REQUEST_DOCUMENTS | 0 | 32 | 0 | 0 |
| RESTRICT | 4 | 1 | 23 | 0 |
| CLOSE_ACCOUNT | 0 | 12 | 0 | 32 |

## Recall by typology and difficulty

| Scenario | n | Recall |
|---|---|---|
| high_risk_jurisdiction | 16 | 100.0% |
| money_mule | 16 | 100.0% |
| mule_cash_out | 12 | 100.0% |
| pass_through | 16 | 100.0% |
| sanctions_exposure | 16 | 87.5% |
| sanctions_transliteration | 12 | 83.3% |
| structuring | 16 | 100.0% |
| *variant: hard* | 33 | 97.0% |
| *variant: standard* | 71 | 95.8% |

## False-positive handling by scenario

| Scenario | n | Recommended close | Auto-closed |
|---|---|---|---|
| cash_intensive_business | 24 | 100.0% | 100.0% |
| new_cash_business | 12 | 100.0% | 0.0% |
| one_off_large_credit | 24 | 95.8% | 95.8% |
| online_retailer | 24 | 100.0% | 100.0% |
| own_account_sweep | 24 | 95.8% | 95.8% |
| sanctions_false_hit | 24 | 100.0% | 0.0% |
| shared_expenses | 12 | 75.0% | 0.0% |

## Missed suspicious alerts (4)

| Alert | Scenario | Variant | Confidence | Final status | Agent's reasoning |
|---|---|---|---|---|---|
| ALR-0023 | sanctions_transliteration | standard | 0.70 | HUMAN_REVIEW_REQUIRED | No red flags found by the standard checks, but no evidenced benign explanation for the alert trigger either |
| ALR-0095 | sanctions_exposure | standard | 0.70 | HUMAN_REVIEW_REQUIRED | No red flags found by the standard checks, but no evidenced benign explanation for the alert trigger either |
| ALR-0142 | sanctions_transliteration | standard | 0.70 | HUMAN_REVIEW_REQUIRED | No red flags found by the standard checks, but no evidenced benign explanation for the alert trigger either |
| ALR-0187 | sanctions_exposure | hard | 0.70 | HUMAN_REVIEW_REQUIRED | No red flags found by the standard checks, but no evidenced benign explanation for the alert trigger either |

## Benign alerts escalated (5)

| Alert | Scenario | Variant | Disposition | Typology given |
|---|---|---|---|---|
| ALR-0016 | own_account_sweep | standard | ESCALATE_L2 | money_mule |
| ALR-0018 | one_off_large_credit | hard | ESCALATE_L2 | money_mule |
| ALR-0041 | shared_expenses | standard | RECOMMEND_SAR | money_mule |
| ALR-0168 | shared_expenses | standard | RECOMMEND_SAR | money_mule |
| ALR-0229 | shared_expenses | hard | RECOMMEND_SAR | money_mule |

## Guardrail violations (0)

None.

## Notes

- Workload estimate assumes 45 min per manual L1 investigation, 12 min to review an agent-prepared case, and 3 min of sampled QA per auto-closed alert.
- The dataset is synthetic with a 40% suspicious rate. Real alert queues are typically over 90% false positives, so precision here overstates production precision.
- Mean processing time 0.061s per alert.
