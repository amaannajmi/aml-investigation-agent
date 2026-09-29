# Agent comparison

Rules v1-v3 and Claude are scored on all alerts. The learned agent is scored only on alerts no analyst decided (it never sees its own training cases), so compare it with the rules on the same held-out alerts.

| Metric | Rules v1 (transactions) | Rules v2 (+ logins, network, disputes) | Rules v3 (+ account activity) | Rules v3, held-out alerts only | Learned from analyst decisions (held-out alerts) |
|---|---|---|---|---|---|
| Alerts evaluated | 248 | 248 | 248 | 124 | 124 |
| Accuracy | 87.1% | 95.6% | 96.4% | 96.8% | 99.2% |
| Precision | 87.5% | 95.1% | 95.2% | 96.1% | 100.0% |
| F1 | 84.0% | 94.7% | 95.7% | 96.1% | 99.0% |
| ROC AUC | 0.923 | 0.977 | 0.980 | 0.979 | 0.998 |
| Account-action accuracy | 81.0% | 91.1% | 91.1% | 93.5% | 95.2% |
| Restrict/close on benign customers | 8.3% | 2.1% | 2.1% | 2.7% | 0.0% |
| Model recall | 80.8% | 94.2% | 96.2% | 96.1% | 98.0% |
| System recall after guardrails | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| False-positive clearance | 91.7% | 96.5% | 96.5% | 97.3% | 100.0% |
| Auto-close rate (benign) | 66.7% | 65.3% | 65.3% | 72.6% | 72.6% |
| Escalation precision | 87.5% | 95.1% | 95.2% | 96.1% | 100.0% |
| Typology accuracy | 90.5% | 98.0% | 98.0% | 98.0% | 98.0% |
| Guardrail violation rate | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |

## Recall by scenario

| Scenario | Rules v1 (transactions) | Rules v2 (+ logins, network, disputes) | Rules v3 (+ account activity) | Rules v3, held-out alerts only | Learned from analyst decisions (held-out alerts) |
|---|---|---|---|---|---|
| high_risk_jurisdiction | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| money_mule | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| mule_cash_out | 58.3% | 83.3% | 100.0% | 100.0% | 100.0% |
| pass_through | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| sanctions_exposure | 81.2% | 87.5% | 87.5% | 85.7% | 85.7% |
| sanctions_transliteration | 0.0% | 83.3% | 83.3% | 83.3% | 100.0% |
| structuring | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |

## False-positive clearance by scenario

| Scenario | Rules v1 (transactions) | Rules v2 (+ logins, network, disputes) | Rules v3 (+ account activity) | Rules v3, held-out alerts only | Learned from analyst decisions (held-out alerts) |
|---|---|---|---|---|---|
| cash_intensive_business | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| new_cash_business | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| one_off_large_credit | 100.0% | 95.8% | 95.8% | 100.0% | 100.0% |
| online_retailer | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| own_account_sweep | 100.0% | 95.8% | 95.8% | 100.0% | 100.0% |
| sanctions_false_hit | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| shared_expenses | 0.0% | 75.0% | 75.0% | 50.0% | 100.0% |
