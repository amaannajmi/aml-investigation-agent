# Evaluation report: baseline-rules-v1

Run `20260929T200941_baseline-rules-v1` · model `deterministic` · 248 alerts (104 suspicious, 144 benign) · auto-close confidence threshold 0.85

## Headline

| Metric | Value | Why it matters |
|---|---|---|
| Model recall (suspicious escalated) | **80.8%** (20 missed) | A missed case is the regulatory risk |
| System recall after guardrails | **100.0%** (0 auto-closed wrongly) | What actually slips past a human |
| False-positive clearance | 91.7% | Benign alerts the agent recommends closing |
| Auto-close rate (benign) | 66.7% | Workload actually removed |
| Escalation precision | 87.5% | Share of escalations that are truly suspicious |
| Typology accuracy (caught cases) | 90.5% | Right reason, not just right answer |
| Guardrail violation rate | 0.0% | Fabricated citations, missing evidence, policy breaches |
| Narrative structure pass rate | 100.0% | Subject, dates, amounts, IDs, typology present |
| Estimated analyst hours | 186.0h → 35.2h (−81.1%) | Illustrative; assumptions below |

## Recall by typology and difficulty

| Scenario | n | Recall |
|---|---|---|
| high_risk_jurisdiction | 16 | 100.0% |
| money_mule | 16 | 100.0% |
| mule_cash_out | 12 | 58.3% |
| pass_through | 16 | 100.0% |
| sanctions_exposure | 16 | 81.2% |
| sanctions_transliteration | 12 | 0.0% |
| structuring | 16 | 100.0% |
| *variant: hard* | 33 | 78.8% |
| *variant: standard* | 71 | 81.7% |

## False-positive handling by scenario

| Scenario | n | Recommended close | Auto-closed |
|---|---|---|---|
| cash_intensive_business | 24 | 100.0% | 100.0% |
| new_cash_business | 12 | 100.0% | 0.0% |
| one_off_large_credit | 24 | 100.0% | 100.0% |
| online_retailer | 24 | 100.0% | 100.0% |
| own_account_sweep | 24 | 100.0% | 100.0% |
| sanctions_false_hit | 24 | 100.0% | 0.0% |
| shared_expenses | 12 | 0.0% | 0.0% |

## Missed suspicious alerts (20)

| Alert | Scenario | Variant | Confidence | Final status | Agent's reasoning |
|---|---|---|---|---|---|
| ALR-0012 | sanctions_transliteration | hard | 0.70 | HUMAN_REVIEW_REQUIRED | No red flags found by the standard checks, but no evidenced benign explanation for the alert trigger either |
| ALR-0023 | sanctions_transliteration | standard | 0.70 | HUMAN_REVIEW_REQUIRED | No red flags found by the standard checks, but no evidenced benign explanation for the alert trigger either |
| ALR-0030 | sanctions_transliteration | standard | 0.70 | HUMAN_REVIEW_REQUIRED | No red flags found by the standard checks, but no evidenced benign explanation for the alert trigger either |
| ALR-0034 | sanctions_transliteration | hard | 0.70 | HUMAN_REVIEW_REQUIRED | No red flags found by the standard checks, but no evidenced benign explanation for the alert trigger either |
| ALR-0045 | mule_cash_out | standard | 0.70 | HUMAN_REVIEW_REQUIRED | No red flags found by the standard checks, but no evidenced benign explanation for the alert trigger either |
| ALR-0095 | sanctions_exposure | standard | 0.70 | HUMAN_REVIEW_REQUIRED | No red flags found by the standard checks, but no evidenced benign explanation for the alert trigger either |
| ALR-0119 | sanctions_transliteration | standard | 0.70 | HUMAN_REVIEW_REQUIRED | No red flags found by the standard checks, but no evidenced benign explanation for the alert trigger either |
| ALR-0140 | sanctions_transliteration | standard | 0.70 | HUMAN_REVIEW_REQUIRED | No red flags found by the standard checks, but no evidenced benign explanation for the alert trigger either |
| ALR-0142 | sanctions_transliteration | standard | 0.70 | HUMAN_REVIEW_REQUIRED | No red flags found by the standard checks, but no evidenced benign explanation for the alert trigger either |
| ALR-0150 | sanctions_transliteration | hard | 0.70 | HUMAN_REVIEW_REQUIRED | No red flags found by the standard checks, but no evidenced benign explanation for the alert trigger either |
| ALR-0159 | mule_cash_out | standard | 0.70 | HUMAN_REVIEW_REQUIRED | No red flags found by the standard checks, but no evidenced benign explanation for the alert trigger either |
| ALR-0161 | sanctions_transliteration | hard | 0.70 | HUMAN_REVIEW_REQUIRED | No red flags found by the standard checks, but no evidenced benign explanation for the alert trigger either |
| ALR-0163 | sanctions_exposure | standard | 0.70 | HUMAN_REVIEW_REQUIRED | No red flags found by the standard checks, but no evidenced benign explanation for the alert trigger either |
| ALR-0174 | sanctions_transliteration | standard | 0.70 | HUMAN_REVIEW_REQUIRED | No red flags found by the standard checks, but no evidenced benign explanation for the alert trigger either |
| ALR-0187 | sanctions_exposure | hard | 0.70 | HUMAN_REVIEW_REQUIRED | No red flags found by the standard checks, but no evidenced benign explanation for the alert trigger either |
| ALR-0188 | mule_cash_out | standard | 0.70 | HUMAN_REVIEW_REQUIRED | No red flags found by the standard checks, but no evidenced benign explanation for the alert trigger either |
| ALR-0196 | sanctions_transliteration | standard | 0.70 | HUMAN_REVIEW_REQUIRED | No red flags found by the standard checks, but no evidenced benign explanation for the alert trigger either |
| ALR-0203 | mule_cash_out | hard | 0.70 | HUMAN_REVIEW_REQUIRED | No red flags found by the standard checks, but no evidenced benign explanation for the alert trigger either |
| ALR-0211 | sanctions_transliteration | standard | 0.70 | HUMAN_REVIEW_REQUIRED | No red flags found by the standard checks, but no evidenced benign explanation for the alert trigger either |
| ALR-0220 | mule_cash_out | hard | 0.70 | HUMAN_REVIEW_REQUIRED | No red flags found by the standard checks, but no evidenced benign explanation for the alert trigger either |

## Benign alerts escalated (12)

| Alert | Scenario | Variant | Disposition | Typology given |
|---|---|---|---|---|
| ALR-0019 | shared_expenses | standard | RECOMMEND_SAR | money_mule |
| ALR-0033 | shared_expenses | standard | RECOMMEND_SAR | money_mule |
| ALR-0041 | shared_expenses | standard | RECOMMEND_SAR | money_mule |
| ALR-0126 | shared_expenses | hard | RECOMMEND_SAR | money_mule |
| ALR-0129 | shared_expenses | standard | RECOMMEND_SAR | money_mule |
| ALR-0155 | shared_expenses | standard | RECOMMEND_SAR | money_mule |
| ALR-0168 | shared_expenses | standard | RECOMMEND_SAR | money_mule |
| ALR-0182 | shared_expenses | standard | RECOMMEND_SAR | money_mule |
| ALR-0214 | shared_expenses | standard | RECOMMEND_SAR | money_mule |
| ALR-0215 | shared_expenses | hard | RECOMMEND_SAR | money_mule |
| ALR-0229 | shared_expenses | hard | RECOMMEND_SAR | money_mule |
| ALR-0240 | shared_expenses | hard | RECOMMEND_SAR | money_mule |

## Guardrail violations (0)

None.

## Notes

- Workload estimate assumes 45 min per manual L1 investigation, 12 min to review an agent-prepared case, and 3 min of sampled QA per auto-closed alert.
- The dataset is synthetic with a 40% suspicious rate. Real alert queues are typically over 90% false positives, so precision here overstates production precision.
- Mean processing time 0.118s per alert.
