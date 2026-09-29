# Agent comparison

| Metric | Rules v1 (transactions) | Rules v2 (+ identity, network, disputes) |
|---|---|---|
| Model recall | 80.8% | 94.2% |
| System recall after guardrails | 100.0% | 100.0% |
| False-positive clearance | 91.7% | 96.5% |
| Auto-close rate (benign) | 66.7% | 65.3% |
| Escalation precision | 87.5% | 95.1% |
| Typology accuracy | 90.5% | 98.0% |
| Guardrail violation rate | 0.0% | 0.0% |

## Recall by scenario

| Scenario | Rules v1 (transactions) | Rules v2 (+ identity, network, disputes) |
|---|---|---|
| high_risk_jurisdiction | 100.0% | 100.0% |
| money_mule | 100.0% | 100.0% |
| mule_cash_out | 58.3% | 83.3% |
| pass_through | 100.0% | 100.0% |
| sanctions_exposure | 81.2% | 87.5% |
| sanctions_transliteration | 0.0% | 83.3% |
| structuring | 100.0% | 100.0% |

## False-positive clearance by scenario

| Scenario | Rules v1 (transactions) | Rules v2 (+ identity, network, disputes) |
|---|---|---|
| cash_intensive_business | 100.0% | 100.0% |
| new_cash_business | 100.0% | 100.0% |
| one_off_large_credit | 100.0% | 95.8% |
| online_retailer | 100.0% | 100.0% |
| own_account_sweep | 100.0% | 95.8% |
| sanctions_false_hit | 100.0% | 100.0% |
| shared_expenses | 0.0% | 75.0% |
