"""Policy guardrails applied to every agent decision, independent of the model.

The agent recommends; policy decides what happens next. These checks are
deliberately simple and deterministic so they can be explained to an auditor.
"""
from __future__ import annotations

import re

from . import config as C
from .schema import AUTO_CLOSE_ELIGIBLE, HUMAN_REVIEW, SAR_PENDING, Decision, GuardrailResult
from .tools import Toolbox

ID_RE = re.compile(r"\b(?:TXN-\d+|LGN-\d+|DSP-\d+|EVT-\d+|CUS-\d+)\b")   # transactions, logins, disputes, events, accounts
TIPPING_OFF_TERMS = ("suspicious", "suspicion", "sar", "money laundering", "laundering", "investigation", "report to",
                     "police", "fiu", "garda", "law enforcement", "fraud")
AMOUNT_RE = re.compile(r"(?:EUR|€)\s?([\d,]+(?:\.\d{1,2})?)")


def _numbers(obj):
    """All numeric values in a nested tool result."""
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        yield float(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _numbers(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _numbers(v)


def apply_guardrails(decision: Decision, tb: Toolbox, screening: dict | None = None) -> GuardrailResult:
    checks, overrides = [], []
    disposition = decision.disposition
    known = tb.all_evidence_ids()
    txns = tb.txn_lookup()

    def check(name, passed, detail=""):
        checks.append({"name": name, "passed": bool(passed), "detail": detail})
        return passed

    # 1. Output contract
    errs = decision.validate()
    check("schema_valid", not errs, "; ".join(errs))

    # 2. Grounding: every ID the agent cites (transaction, login, dispute, linked account) must exist and be
    #    reachable from this customer's case
    cited = set(decision.evidence_txn_ids) | set(ID_RE.findall(decision.narrative)) | set(ID_RE.findall(decision.rationale)) \
        | {str(f.get("id")) for f in decision.flagged_items if f.get("id")}
    fabricated = sorted(cited - known)
    check("citations_grounded", not fabricated,
          f"cited IDs not found in this case: {fabricated}" if fabricated else f"{len(cited)} citations verified")

    # 3. Grounding: every EUR amount quoted in the narrative must be traceable to a transaction, the sum of
    #    the cited transactions, or a figure produced by a tool (KYC expected activity, computed aggregates).
    if decision.narrative:
        cited_amounts = [txns[t]["amount_eur"] for t in cited if t in txns]
        targets = {v["amount_eur"] for v in txns.values()} | {sum(cited_amounts)}
        targets |= set(_numbers(tb.compute_risk_indicators())) | set(_numbers(tb.get_customer_profile()))
        targets |= {C.CASH_REPORTING_THRESHOLD, C.NEAR_THRESHOLD_FLOOR, C.LARGE_CREDIT_MIN}   # policy reference figures
        quoted = [float(a.replace(",", "")) for a in AMOUNT_RE.findall(decision.narrative)]
        untraceable = [q for q in quoted if not any(abs(q - t) <= max(1.0, 0.005 * abs(t)) for t in targets)]
        check("amounts_traceable", not untraceable,
              f"amounts not traceable to data: {untraceable}" if untraceable else f"{len(quoted)} amounts verified")

    # 4. Escalations must carry evidence
    if decision.is_escalation:
        check("escalation_has_evidence", len(decision.evidence_txn_ids) > 0, "")

    # 5. Hard policy: a strong sanctions match with matching country can never be closed by the agent
    if screening is None:
        screening = tb.screen_counterparties()
    strong = [h for h in screening["results"] if h["is_potential_match"] and h["country_matches"]]
    if strong and disposition == "CLOSE_FALSE_POSITIVE":
        disposition = "ESCALATE_L2"
        overrides.append(f"Sanctions policy override: strong match {strong[0]['screened_name']} ~ "
                         f"{strong[0]['list_name']} ({strong[0]['score']}) cannot be closed by an agent")
    check("sanctions_policy", not (strong and decision.disposition == "CLOSE_FALSE_POSITIVE"),
          overrides[-1] if overrides else "")

    # 6. The account action must agree with the disposition
    if decision.account_action in ("RESTRICT", "CLOSE_ACCOUNT") and disposition == "CLOSE_FALSE_POSITIVE":
        check("action_consistent", False, f"{decision.account_action} recommended on an alert recommended for closure")
    if decision.account_action in ("RESTRICT", "CLOSE_ACCOUNT"):
        overrides.append(f"{decision.account_action.replace('_', ' ').title()} is a recommendation: it needs analyst "
                         f"approval{' and MLRO sign-off' if decision.account_action == 'CLOSE_ACCOUNT' else ''}")

    # 7. Tipping-off: a document request sent while a SAR is being considered must not reveal the suspicion
    if decision.account_action == "REQUEST_DOCUMENTS" or decision.documents_requested:
        wording = " ".join(decision.documents_requested).lower()
        leaks = [w for w in TIPPING_OFF_TERMS if re.search(rf"\b{re.escape(w)}\b", wording)]
        check("tipping_off_wording", not leaks, f"request wording mentions: {leaks}" if leaks else "neutral wording")
        if decision.is_escalation:
            overrides.append("Document request during a suspicion review: MLRO to approve wording before it is sent (tipping-off)")

    # Final routing. The agent never files or closes on its own authority.
    hard_fail = any(not c["passed"] for c in checks)
    if disposition == "RECOMMEND_SAR":
        status = SAR_PENDING
    elif disposition == "ESCALATE_L2":
        status = HUMAN_REVIEW
    elif hard_fail:
        status = HUMAN_REVIEW
        overrides.append("Close recommendation routed to human: guardrail check failed")
    elif decision.confidence < C.AUTO_CLOSE_MIN_CONFIDENCE:
        status = HUMAN_REVIEW
        overrides.append(f"Close recommendation routed to human: confidence {decision.confidence:.2f} "
                         f"< {C.AUTO_CLOSE_MIN_CONFIDENCE:.2f}")
    else:
        status = AUTO_CLOSE_ELIGIBLE
    return GuardrailResult(final_status=status, final_disposition=disposition, checks=checks, overrides=overrides)
