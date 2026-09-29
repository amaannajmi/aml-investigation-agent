"""Deterministic baseline agent.

Follows a written L1 investigation procedure using the same tools as the LLM
agent. It needs no API key, is fully reproducible, and gives the LLM agent a
benchmark to beat. Its rules are the kind an experienced analyst would write
down on day one, not tuned to the test set.
"""
from __future__ import annotations

from ..audit import AuditLog
from ..schema import Decision
from ..tools import Toolbox


# which benign-explanation checks are relevant to which alert rule
RULE_SIGNALS = {
    "TM-CASH-01": {"cash"}, "TM-RMF-02": {"pass_through"}, "TM-LGC-06": {"pass_through"},
    "TM-FAN-03": {"fan_in"}, "TM-HRJ-04": set(), "SCR-PAY-05": {"sanctions"}, "TM-HVW-07": set(),
}


def _eur(x: float) -> str:
    return f"EUR {x:,.2f}"


class BaselineAgent:
    name = "baseline-rules"
    model = "deterministic"

    def investigate(self, tb: Toolbox, audit: AuditLog) -> tuple[Decision, dict]:
        alert = audit.tool_call(tb, "get_alert")
        prof = audit.tool_call(tb, "get_customer_profile")
        ind = audit.tool_call(tb, "compute_risk_indicators")
        scr = audit.tool_call(tb, "screen_counterparties")
        txns = tb.txn_lookup()

        findings = []   # (disposition, typology, confidence, indicator text, evidence ids)
        benign = []     # (signal, confidence, explanation)
        exp = ind["expected_monthly_inflow_eur"]
        occ = str(prof["occupation"]).lower()
        low_income_profile = prof["customer_type"] == "Individual" and any(w in occ for w in ("student", "unemployed", "part-time"))

        # --- Sanctions --------------------------------------------------------------
        for h in scr["results"]:
            if not h["is_potential_match"]:
                continue
            if h["country_matches"]:
                findings.append(("RECOMMEND_SAR", "sanctions_exposure", 0.9,
                                 f"Counterparty '{h['screened_name']}' matches sanctioned party '{h['list_name']}' "
                                 f"({h['programme']}), score {h['score']}, same country {h['list_country']}", h["txn_ids"]))
            else:
                benign.append(("sanctions", 0.86, f"Screening hit '{h['screened_name']}' ({h['screened_country']}) vs '{h['list_name']}' "
                                     f"({h['list_country']}) discounted: country mismatch, score {h['score']}"))

        # --- Structuring ------------------------------------------------------------
        cash = ind["cash"]
        habitual = cash["deposits_history_monthly_avg"] >= 4
        if cash["near_threshold_window_count"] >= 3 and not habitual and cash["deposits_window_eur"] > 1.5 * exp:
            findings.append(("RECOMMEND_SAR", "structuring", 0.85,
                             f"{cash['near_threshold_window_count']} cash deposits between EUR 7,000 and EUR 10,000 in 30 days "
                             f"(prior avg {cash['near_threshold_history_monthly_avg']}/month); cash total "
                             f"{_eur(cash['deposits_window_eur'])} vs expected monthly inflow {_eur(exp)}", cash["cash_txn_ids"]))
        elif cash["deposits_window_count"] >= 3 and not habitual and cash["deposits_window_eur"] > 3 * exp:
            findings.append(("ESCALATE_L2", "structuring", 0.65,
                             f"{cash['deposits_window_count']} cash deposits totalling {_eur(cash['deposits_window_eur'])}, "
                             f"{cash['deposits_window_eur'] / exp:.1f}x expected monthly inflow, not seen in prior history",
                             cash["cash_txn_ids"]))
        elif cash["deposits_window_count"] and habitual:
            benign.append(("cash", 0.9, f"Cash deposits consistent with established pattern "
                                f"({cash['deposits_history_monthly_avg']}/month in prior 60 days) for a {prof['occupation']}"))

        # --- Pass-through / rapid movement ---------------------------------------------------
        pt, to = ind["pass_through"], ind["turnover"]
        ext_out_ratio = to["outflow_external_window_eur"] / max(to["inflow_window_eur"], 1)
        if pt["external_pass_through_ratio"] >= 0.75 and pt["large_credits_eur"] > 2 * exp:
            disp = "RECOMMEND_SAR" if pt["external_pass_through_ratio"] >= 0.9 else "ESCALATE_L2"
            findings.append((disp, "pass_through", 0.85 if disp == "RECOMMEND_SAR" else 0.7,
                             f"{pt['external_pass_through_ratio']:.0%} of {_eur(pt['large_credits_eur'])} in large credits left "
                             f"to third parties within {pt['window_hours']}h; credits are "
                             f"{pt['large_credits_eur'] / exp:.1f}x expected monthly inflow", pt["evidence_txn_ids"]))
        elif to["inflow_vs_expected_ratio"] >= 3 and ext_out_ratio >= 0.75 and to["external_outflow_countries"]:
            findings.append(("ESCALATE_L2", "pass_through", 0.6,
                             f"Inflow {to['inflow_vs_expected_ratio']}x expected; {ext_out_ratio:.0%} paid out externally to "
                             f"{', '.join(to['external_outflow_countries'])} with little retained", pt["large_credit_txn_ids"] + to["outflow_external_txn_ids"]))
        elif pt["own_account_transfer_ratio"] >= 0.5:
            benign.append(("pass_through", 0.9, f"Onward movement is to the customer's own account "
                                f"({pt['own_account_transfer_ratio']:.0%} of large credits); no third-party pass-through"))
        elif pt["large_credits_eur"] > 2 * exp and ext_out_ratio < 0.3:
            refs = " ".join(str(txns[t]["counterparty_name"]) + " " + str(txns[t]["reference"]) for t in pt["large_credit_txn_ids"]).lower()
            documented = any(k in refs for k in ("solicitor", "property", "estate", "sale", "revenue"))
            benign.append(("pass_through", 0.87 if documented else 0.72,
                           "Large credit retained in the account" + (" with a documented source (solicitor/property sale)" if documented else "")))

        # --- Money mule / fan-in -------------------------------------------------------------
        cp = ind["counterparties"]
        spike = cp["unique_inbound_window"] > 3 * max(cp["unique_inbound_history_monthly_avg"], 1)
        if prof["customer_type"] == "Individual" and cp["unique_inbound_window"] >= 10 and spike and \
                (cp["crypto_outflow_share_of_inflow"] >= 0.5 or low_income_profile):
            findings.append(("RECOMMEND_SAR", "money_mule", 0.85,
                             f"{cp['unique_inbound_window']} distinct payers in 30 days (prior avg "
                             f"{cp['unique_inbound_history_monthly_avg']}/month); {cp['crypto_outflow_share_of_inflow']:.0%} "
                             f"of inflow sent to crypto exchange", cp["crypto_txn_ids"] + cp["inbound_small_txn_ids"][:10]))
        elif cp["unique_inbound_window"] >= 6 and cp["crypto_outflow_share_of_inflow"] >= 0.5:
            findings.append(("ESCALATE_L2", "money_mule", 0.65,
                             f"{cp['unique_inbound_window']} distinct payers, {cp['crypto_outflow_share_of_inflow']:.0%} "
                             f"forwarded to crypto exchange", cp["crypto_txn_ids"] + cp["inbound_small_txn_ids"][:10]))
        elif cp["unique_inbound_window"] >= 10 and not spike:
            benign.append(("fan_in", 0.88, f"Many small payers ({cp['unique_inbound_window']}) is consistent with the customer's history "
                                 f"({cp['unique_inbound_history_monthly_avg']}/month) and business model ({prof['occupation']})"))

        # --- High-risk jurisdictions -------------------------------------------------------------
        j = ind["jurisdictions"]
        undeclared = [c for c in j["countries"] if c not in j["declared_ties"]]
        if undeclared:
            findings.append(("ESCALATE_L2", "high_risk_jurisdiction", 0.8,
                             f"{j['high_risk_txn_count']} payment(s) totalling {_eur(j['high_risk_eur'])} with "
                             f"{', '.join(undeclared)} (FATF call-for-action), no declared ties", j["high_risk_txn_ids"]))

        return self._decide(tb, alert, prof, findings, benign), {"screening": scr, "indicators": ind}

    # ----------------------------------------------------------------------------------------
    def _decide(self, tb, alert, prof, findings, benign) -> Decision:
        txns = tb.txn_lookup()
        if findings:
            rank = {"RECOMMEND_SAR": 2, "ESCALATE_L2": 1}
            findings.sort(key=lambda f: (-rank[f[0]], -f[2]))
            disp, _, conf, _, _ = findings[0]
            if len(findings) > 1:
                conf = min(0.95, conf + 0.05)
            typs = list(dict.fromkeys(f[1] for f in findings))
            ev = list(dict.fromkeys(t for f in findings for t in f[4]))[:25]
            indicators = [f[3] for f in findings]
            amt = sum(txns[t]["amount_eur"] for t in ev if t in txns)
            first = min((txns[t]["timestamp"] for t in ev if t in txns), default=None)
            last = max((txns[t]["timestamp"] for t in ev if t in txns), default=None)
            narrative = (
                f"Subject: {prof['customer_name']} ({prof['customer_id']}), {prof['customer_type'].lower()}, "
                f"occupation/activity: {prof['occupation']}, expected monthly inflow {_eur(prof['expected_monthly_inflow_eur'])}. "
                f"Alert {alert['alert_id']} ({alert['rule_name']}) was reviewed over the 30-day lookback. "
                f"Between {str(first)[:10]} and {str(last)[:10]}, {len(ev)} transactions totalling {_eur(amt)} "
                f"({', '.join(ev[:8])}{'...' if len(ev) > 8 else ''}) were identified as relevant. "
                f"Indicators: " + " | ".join(indicators) + ". "
                f"Suspected typology: {', '.join(typs).replace('_', ' ')}. The activity is inconsistent with the customer's "
                f"stated profile and no legitimate explanation was identified in the available data."
            )
            return Decision(alert_id=alert["alert_id"], disposition=disp, typologies=typs, confidence=round(conf, 2),
                            risk_indicators=indicators, evidence_txn_ids=ev,
                            rationale=f"{len(findings)} red flag(s); highest severity drives the disposition.",
                            narrative=narrative, agent=self.name, model=self.model)
        # only a benign explanation that addresses what triggered the alert counts
        relevant = [b for b in benign if b[0] in RULE_SIGNALS.get(alert["rule_id"], set())]
        conf = min((b[1] for b in relevant), default=0.7)
        why = [b[2] for b in relevant] or ["No red flags found by the standard checks, but no evidenced benign "
                                          "explanation for the alert trigger either"]
        return Decision(alert_id=alert["alert_id"], disposition="CLOSE_FALSE_POSITIVE", typologies=["none"],
                        confidence=round(conf, 2), risk_indicators=[], evidence_txn_ids=[],
                        rationale=" | ".join(why),
                        narrative=f"Alert {alert['alert_id']} ({alert['rule_name']}) on {prof['customer_name']} "
                                  f"({prof['customer_id']}) reviewed. " + " ".join(why) + ". Recommend close as false positive.",
                        agent=self.name, model=self.model)
