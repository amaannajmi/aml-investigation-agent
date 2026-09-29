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


def sh_list(link: dict) -> list[dict]:
    return link.get("shared", [])


def _eur(x: float) -> str:
    return f"EUR {x:,.2f}"


class BaselineAgent:
    """level 1: transactions only (v1). level 2: + access, network and dispute rules (v2).
    level 3: + account activity (credential/contact changes, financials added and removed) (v3)."""
    model = "deterministic"

    def __init__(self, level: int = 3, use_identity: bool | None = None):
        if use_identity is not None:            # backwards compatible
            level = 3 if use_identity else 1
        self.level = level
        self.use_identity = level >= 2
        self.name = f"baseline-rules-v{level}"

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

        if self.use_identity and "network" in ind:
            self._identity_rules(tb, audit, alert, ind, scr, findings, benign)
        if self.level >= 3 and "activity" in ind:
            self._activity_rules(tb, audit, alert, ind, findings)

        return self._decide(tb, alert, prof, findings, benign), {"screening": scr, "indicators": ind}

    # --- v3: account activity -------------------------------------------------------------------------
    def _activity_rules(self, tb, audit, alert, ind, findings):
        audit.tool_call(tb, "get_financials")
        audit.tool_call(tb, "get_account_activity")
        act, acc = ind["activity"], ind["access"]
        typ = "pass_through" if alert["rule_id"] == "TM-RMF-02" else "money_mule"
        if act["financial_added_then_removed_window"]:
            findings.append(("RECOMMEND_SAR", typ, 0.85,
                             "A withdrawal account was added in the window and removed again after funds were paid out to it",
                             act["risky_event_ids"]))
        if act["credential_or_contact_changes_window"] and act["events_from_new_device_or_abroad"] and acc["new_devices_window"]:
            text = ("Password reset and/or phone/email change made from a device first seen in the window: "
                    "the account may have been sold, rented out or taken over")
            if findings:
                d, t, c, i, e = findings[0]
                findings[0] = (d, t, min(0.95, c + 0.05), i + "; " + text, e + act["risky_event_ids"])
            elif alert["rule_id"] in ("TM-RMF-02", "TM-FAN-03"):
                findings.append(("ESCALATE_L2", typ, 0.65, text, act["risky_event_ids"] + acc["evidence_login_ids"][:3]))

    # --- v2: access, network and dispute evidence -------------------------------------------------------
    def _identity_rules(self, tb, audit, alert, ind, scr, findings, benign):
        audit.tool_call(tb, "get_identity_profile")
        audit.tool_call(tb, "get_login_history", {"limit": 10})
        links = audit.tool_call(tb, "get_linked_accounts")["linked_accounts"]
        audit.tool_call(tb, "get_disputes")
        acc, net, dsp = ind["access"], ind["network"], ind["disputes"]
        strong_types = {"device_id", "bank_fingerprint", "phone", "email"}

        # 1. Linked to accounts we have already closed, limited or reported
        if net["adverse_links_medium_or_strong"]:
            shared = net["adverse_shared_identifier_types"]
            strong = bool(strong_types & set(shared))
            typ = (net["adverse_typologies"] or ["money_mule"])[0]
            findings.append(("RECOMMEND_SAR" if strong and net["adverse_with_prior_sar"] else "ESCALATE_L2", typ,
                             0.85 if strong else 0.7,
                             f"Shares {', '.join(s.replace('_', ' ') for s in shared)} with {net['adverse_links_medium_or_strong']} "
                             f"closed/limited account(s) ({', '.join(net['adverse_linked_ids'][:4])}), "
                             f"{net['adverse_with_prior_sar']} with a prior SAR", net["adverse_linked_ids"] + acc["evidence_login_ids"][:4]))

        # 2. Payers saying their money was taken or they were scammed: this account may be receiving victim funds
        if dsp["victim_claims"]:
            findings.append(("RECOMMEND_SAR" if dsp["victim_claims"] >= 2 else "ESCALATE_L2", "money_mule",
                             0.85 if dsp["victim_claims"] >= 2 else 0.65,
                             f"{dsp['victim_claims']} payer claim(s) of unauthorised or scam payments "
                             f"({', '.join(dsp['victim_claim_ids'][:5])})", dsp["victim_claim_ids"] + dsp["victim_claim_txn_ids"]))

        # 3. Someone new appears to be operating the account
        takeover = acc["new_device_login_share_window"] >= 0.6 and \
            (acc["foreign_login_share_window"] >= 0.5 or acc["vpn_hosting_login_share_window"] >= 0.3)
        if takeover:
            text = (f"Account operated from a new device in the window ({acc['new_device_login_share_window']:.0%} of logins); "
                    f"foreign logins from {', '.join(acc['foreign_login_countries_window']) or 'n/a'}, "
                    f"{acc['vpn_hosting_login_share_window']:.0%} via hosting/VPN")
            if findings:
                findings[:] = [(d, t, min(0.95, c + 0.05), i + ("; " + text if k == 0 else ""), e)
                               for k, (d, t, c, i, e) in enumerate(findings)]
            elif alert["rule_id"] in ("TM-RMF-02", "TM-FAN-03"):
                findings.append(("ESCALATE_L2", "pass_through" if alert["rule_id"] == "TM-RMF-02" else "money_mule", 0.65,
                                 text, acc["evidence_login_ids"]))

        # 4. Near-miss sanctions name plus logins from a sanctioned country
        if acc["sanctioned_country_logins_window"]:
            near = [h for h in scr["results"] if not h["is_potential_match"] and h["country_matches"] and h["score"] >= 70]
            if near:
                h = near[0]
                findings.append(("ESCALATE_L2", "sanctions_exposure", 0.7,
                                 f"Payee '{h['screened_name']}' is a near-miss ({h['score']}) for sanctioned '{h['list_name']}' "
                                 f"({h['list_country']}), and the account was accessed from a sanctioned country "
                                 f"{acc['sanctioned_country_logins_window']} time(s) in the window",
                                 h["txn_ids"] + acc["evidence_login_ids"][:3]))

        # 5. Fan-in with no corroboration, where some payers share the customer's household: benign explanation
        fan_in_only = [f for f in findings if f[1] == "money_mule"] and not dsp["victim_claims"] and \
            not net["adverse_links_medium_or_strong"] and not acc["new_devices_window"]
        if fan_in_only:
            payers = {str(v["counterparty_name"]) for v in tb.txn_lookup().values() if v["direction"] == "IN"}
            housemates = [l for l in links if not l["is_adverse"] and l["customer_name"] in payers and
                          any(sh["identifier_type"] == "ip" and sh["strength"] == "medium" for sh in sh_list(l))]
            if housemates:
                findings[:] = [f for f in findings if f[1] != "money_mule"]
                benign.append(("fan_in", 0.8, f"{len(housemates)} of the payers ({', '.join(h['customer_id'] for h in housemates)}) "
                                              f"share the customer's home IP and are clean accounts, consistent with a shared household; "
                                              f"no victim claims, no new devices, no links to actioned accounts"))
            else:
                findings[:] = [("ESCALATE_L2", t, 0.55, i + "; no network, device or victim-claim corroboration", e)
                               if t == "money_mule" and d == "RECOMMEND_SAR" else (d, t, c, i, e) for d, t, c, i, e in findings]

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
            ev = list(dict.fromkeys(t for f in findings for t in f[4]))[:30]
            indicators = [f[3] for f in findings]
            tx_ev = [t for t in ev if t in txns]
            other_ev = [t for t in ev if t not in txns]
            amt = sum(txns[t]["amount_eur"] for t in tx_ev)
            first = min((txns[t]["timestamp"] for t in ev if t in txns), default=None)
            last = max((txns[t]["timestamp"] for t in ev if t in txns), default=None)
            narrative = (
                f"Subject: {prof['customer_name']} ({prof['customer_id']}), {prof['customer_type'].lower()}, "
                f"occupation/activity: {prof['occupation']}, expected monthly inflow {_eur(prof['expected_monthly_inflow_eur'])}. "
                f"Alert {alert['alert_id']} ({alert['rule_name']}) was reviewed over the 30-day lookback. "
                f"Between {str(first)[:10]} and {str(last)[:10]}, {len(tx_ev)} transactions totalling {_eur(amt)} "
                f"({', '.join(tx_ev[:8])}{'...' if len(tx_ev) > 8 else ''}) were identified as relevant. "
                + (f"Other evidence: {', '.join(other_ev[:8])}. " if other_ev else "") +
                f"Indicators: " + " | ".join(indicators) + ". "
                f"Suspected typology: {', '.join(typs).replace('_', ' ')}. The activity is inconsistent with the customer's "
                f"stated profile and no legitimate explanation was identified in the available data."
            )
            action, action_reason, docs = self._account_action(disp, typs, conf, tb)
            return Decision(alert_id=alert["alert_id"], disposition=disp, typologies=typs, confidence=round(conf, 2),
                            risk_indicators=indicators, evidence_txn_ids=ev,
                            rationale=f"{len(findings)} red flag(s); highest severity drives the disposition.",
                            narrative=narrative, agent=self.name, model=self.model,
                            account_action=action, account_action_reason=action_reason, documents_requested=docs,
                            sar_reasoning=self._sar_reasoning(disp, typs, indicators),
                            flagged_items=self._flagged(findings, tb)[:20])
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
                        agent=self.name, model=self.model,
                        account_action="NO_ACTION", account_action_reason="No grounds for restriction: " + why[0],
                        sar_reasoning="No SAR: " + ("a benign explanation is evidenced." if relevant else
                                                    "no red flags found; an analyst confirms before closing."))

    # --- account action, documents, SAR reasoning, per-item reasons --------------------------------------
    DOCS = {
        "structuring": ["Source of funds for the cash deposited (for example payslips, sale receipts or business takings records)",
                        "Explanation of why the funds were deposited in several separate amounts"],
        "high_risk_jurisdiction": ["Purpose of the payments and relationship to the beneficiary",
                                   "Supporting invoices, contracts or other documents for the payments"],
        "money_mule": ["Explanation of the incoming payments and your relationship to the payers",
                       "Proof of identity (photo ID and a selfie) to confirm you are the account holder"],
        "pass_through": ["Explanation of the business purpose of the incoming and outgoing transfers",
                         "Invoices or contracts supporting the transfers"],
        "sanctions_exposure": ["Full name, address and registration details of the payee",
                               "Contract and invoices for the payment"],
    }

    def _account_action(self, disp, typs, conf, tb):
        t = typs[0]
        if disp == "RECOMMEND_SAR" and t in ("money_mule", "pass_through") and conf >= 0.85:
            return ("CLOSE_ACCOUNT", f"Strong evidence the account is being used to move third-party funds ({t.replace('_', ' ')}): "
                                     "exit the relationship after the SAR decision; restrict outgoing payments meanwhile", [])
        if t == "sanctions_exposure":
            return ("RESTRICT", "Potential sanctions exposure: freeze the affected funds and restrict outgoing payments until the "
                                "match is confirmed or discounted", self.DOCS[t])
        if t in ("structuring", "high_risk_jurisdiction") or disp == "ESCALATE_L2":
            return ("REQUEST_DOCUMENTS", "Grounds to understand the activity before deciding: request supporting documents",
                    self.DOCS.get(t, self.DOCS["money_mule"]))
        return ("RESTRICT", "Suspicious activity reported: restrict outgoing payments pending review", [])

    @staticmethod
    def _sar_reasoning(disp, typs, indicators):
        t = ", ".join(typs).replace("_", " ")
        if disp == "RECOMMEND_SAR":
            return (f"File a SAR: reasonable grounds to suspect {t} ({indicators[0][:160]}). File without delay once the MLRO "
                    f"approves, before or alongside any account exit, and do not tell the customer a report is being made.")
        return (f"No SAR yet: red flags for {t} need L2 review. The SAR decision must be made as soon as the review "
                f"establishes, or rules out, reasonable grounds for suspicion.")

    @staticmethod
    def _flagged(findings, tb):
        kinds = {"TXN": "transaction", "LGN": "login", "DSP": "dispute", "EVT": "account activity", "CUS": "linked account"}
        out, seen = [], set()
        for _, _, _, reason, ev in findings:
            for i in ev:
                if i not in seen:
                    seen.add(i)
                    out.append({"id": i, "kind": kinds.get(i.split("-")[0], "item"), "reason": reason[:200]})
        return out
