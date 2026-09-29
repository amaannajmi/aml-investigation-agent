"""LLM investigation agent using Claude tool use.

The model plans its own investigation: it chooses which tools to call and in
what order, reasons over the results, and must finish by calling
`submit_decision` with a structured recommendation. It has read-only access
to one alert's data and no ability to act. Guardrails run afterwards.

Requires ANTHROPIC_API_KEY. Model is set by AML_AGENT_MODEL (see config.py).
"""
from __future__ import annotations

import json

from .. import config as C
from ..audit import AuditLog
from ..schema import ACCOUNT_ACTIONS, DISPOSITIONS, TYPOLOGIES, Decision
from ..tools import TOOL_SPECS, Toolbox

SYSTEM_PROMPT = """You are an L1 AML investigator at a regulated payments firm. You investigate one transaction-monitoring alert at a time and recommend a disposition to a human analyst. You never file reports or close cases yourself.

Investigation procedure:
1. Read the alert and the customer's KYC profile. Note the expected activity.
2. Pull the behavioural indicators and the transactions for the alert window. Compare with the customer's OWN history (period='history'): a pattern that has always existed and fits the profile is weaker evidence than a sudden change.
3. Screen counterparties. A potential sanctions match where the country also matches must never be closed. Near-misses (score 70-88, same country) deserve a close look: transliterations and abbreviations of sanctioned names often score low.
3b. Check who controls the account and who it is connected to:
   - get_identity_profile and get_login_history: is a new device operating the account in the alert window? Logins from abroad, via hosting/VPN IPs, or from sanctioned countries?
   - get_linked_accounts: link strength matters. A shared IP alone is weak evidence (households, offices, mobile carriers, public wi-fi and VPNs are shared); a shared device, bank account, phone or email with an account we closed or reported is strong. Check timing too: a device used by a closed account months before this customer ever used it may simply be a second-hand phone.
   - get_financials and get_account_activity: a withdrawal account added and then removed after use, or a password reset and phone change from a new device, suggest the account was sold, rented out or taken over.
   - get_disputes: payers saying their payment was unauthorised or a scam suggests this account received victim funds. Read the status: a withdrawn claim carries little weight. Buyer disputes on a retailer are normal at low rates.
4. Actively look for a legitimate explanation (own-account transfers, documented sale proceeds, a business model that explains many small payers, a longstanding counterparty, payers who share the customer's household). Only close when you have found one, not merely because red flags are absent.
5. Decide:
   - CLOSE_FALSE_POSITIVE: a specific, evidenced benign explanation exists.
   - ESCALATE_L2: red flags present but not conclusive, or you cannot rule out suspicion.
   - RECOMMEND_SAR: clear indicators of a known typology (structuring, pass_through, money_mule, high_risk_jurisdiction, sanctions_exposure).
6. Missing a genuinely suspicious case is far more costly than escalating a benign one. When in doubt, escalate.
7. Recommend what should happen to the account (a human approves every action):
   - NO_ACTION: benign.
   - REQUEST_DOCUMENTS: you need the customer's explanation or evidence (source of funds, purpose of payments, proof of identity if the account may have changed hands).
   - RESTRICT: freeze or limit outgoing payments while the case is reviewed (sanctions exposure, suspected takeover, funds that may belong to victims).
   - CLOSE_ACCOUNT ("ban"): strong evidence the account is used to move third-party or criminal funds.
   Give the reason in account_action_reason.
8. Tipping-off: never reveal a suspicion or a report to the customer. Document requests must be neutral, ordinary account-review wording, with no mention of suspicion, SAR, laundering, fraud, investigation or police.
9. In sar_reasoning, say whether a SAR is needed, why (the grounds for suspicion), and when (without delay once the MLRO approves; before or alongside any exit; for escalations, as soon as the L2 review establishes or rules out grounds).
10. In flagged_items, list each transaction, login, account event, dispute or linked account that matters, with a one-line reason why it is suspicious (or, for closures, why it is explained).
11. risk_score: your calibrated probability that the activity is genuinely suspicious (used to rank cases).

Evidence rules (these are checked automatically and violations are routed to a human):
- Cite only IDs that appeared in tool results: transactions (TXN-), logins (LGN-), account events (EVT-), disputes (DSP-) and linked accounts (CUS-). Never invent IDs, amounts or dates.
- Put every cited ID in evidence_txn_ids as well as the narrative.
- Every amount in the narrative must come from a transaction or a sum of cited transactions.
- Confidence is your calibrated probability that the disposition is correct.

The narrative should read like the grounds-for-suspicion section of a SAR: who, what, when, how much, why it is unusual for this customer, and which typology it indicates. For closures, state the benign explanation and the evidence for it. Be concise."""

SUBMIT_TOOL = {
    "name": "submit_decision",
    "description": "Submit your final recommendation. Call exactly once, when the investigation is complete.",
    "input_schema": {
        "type": "object",
        "required": ["disposition", "typologies", "confidence", "risk_indicators", "evidence_txn_ids", "rationale", "narrative",
                     "account_action", "account_action_reason", "sar_reasoning", "flagged_items", "risk_score"],
        "properties": {
            "disposition": {"type": "string", "enum": list(DISPOSITIONS)},
            "typologies": {"type": "array", "items": {"type": "string", "enum": list(TYPOLOGIES)}},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "risk_indicators": {"type": "array", "items": {"type": "string"}},
            "evidence_txn_ids": {"type": "array", "items": {"type": "string"}, "description": "Every cited ID: TXN-, LGN-, DSP- or CUS-."},
            "rationale": {"type": "string", "description": "Short internal reasoning for the reviewer."},
            "narrative": {"type": "string", "description": "SAR-style grounds for suspicion, or the evidenced benign explanation."},
            "account_action": {"type": "string", "enum": list(ACCOUNT_ACTIONS)},
            "account_action_reason": {"type": "string"},
            "documents_requested": {"type": "array", "items": {"type": "string"},
                                    "description": "Neutral wording only: no mention of suspicion or reporting."},
            "sar_reasoning": {"type": "string", "description": "Whether a SAR is needed, why, and when."},
            "flagged_items": {"type": "array", "items": {"type": "object", "required": ["id", "kind", "reason"], "properties": {
                "id": {"type": "string"}, "kind": {"type": "string"}, "reason": {"type": "string"}}}},
            "risk_score": {"type": "number", "minimum": 0, "maximum": 1},
        },
    },
}


class ClaudeAgent:
    name = "claude-tool-use"

    def __init__(self, model: str | None = None, client=None, max_turns: int = C.LLM_MAX_TURNS, use_feedback: bool = True):
        self.model = model or C.LLM_MODEL
        self.use_feedback = use_feedback
        self.max_turns = max_turns
        if client is None:
            import anthropic
            client = anthropic.Anthropic()
        self.client = client

    def investigate(self, tb: Toolbox, audit: AuditLog) -> tuple[Decision, dict]:
        messages = [{"role": "user", "content": f"Investigate alert {tb.alert['alert_id']} and submit your decision."}]
        system = SYSTEM_PROMPT
        if self.use_feedback:
            from ..learning import feedback_examples
            lessons = [x for x in feedback_examples() if x["alert_id"] != tb.alert["alert_id"]]
            if lessons:
                system += ("\n\nLessons from analyst decisions on earlier cases where the analyst disagreed with the agent. "
                           "Treat them as guidance on judgement, not as facts about this case:\n"
                           + "\n".join(f"- {x['alert_id']}: agent said {x['agent_said']}; analyst decided {x['analyst_decided']}. "
                                        f"Reason: {x['analyst_reason']}" for x in lessons))
                audit.log("llm_feedback_lessons", count=len(lessons), alert_ids=[x["alert_id"] for x in lessons])
        tools = TOOL_SPECS + [SUBMIT_TOOL]
        ctx: dict = {"usage": {"input_tokens": 0, "output_tokens": 0}, "turns": 0}
        audit.log("llm_start", model=self.model)

        for turn in range(self.max_turns):
            resp = self.client.messages.create(model=self.model, max_tokens=4096, system=system,
                                               tools=tools, messages=messages)
            ctx["turns"] = turn + 1
            usage = getattr(resp, "usage", None)
            if usage:
                ctx["usage"]["input_tokens"] += getattr(usage, "input_tokens", 0) or 0
                ctx["usage"]["output_tokens"] += getattr(usage, "output_tokens", 0) or 0
            messages.append({"role": "assistant", "content": resp.content})
            tool_uses = [b for b in resp.content if getattr(b, "type", None) == "tool_use"]
            texts = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
            if texts:
                audit.log("llm_reasoning", text="\n".join(texts)[:4000])

            if not tool_uses:
                messages.append({"role": "user", "content": "You must finish by calling submit_decision."})
                continue

            results = []
            for tu in tool_uses:
                if tu.name == "submit_decision":
                    d = dict(tu.input)
                    audit.log("llm_submit", decision=d)
                    decision = Decision(alert_id=tb.alert["alert_id"], disposition=d.get("disposition", ""),
                                        typologies=list(d.get("typologies", [])), confidence=float(d.get("confidence", 0)),
                                        risk_indicators=list(d.get("risk_indicators", [])),
                                        evidence_txn_ids=list(d.get("evidence_txn_ids", [])),
                                        rationale=d.get("rationale", ""), narrative=d.get("narrative", ""),
                                        agent=self.name, model=self.model,
                                        account_action=d.get("account_action", "NO_ACTION"),
                                        account_action_reason=d.get("account_action_reason", ""),
                                        documents_requested=list(d.get("documents_requested", [])),
                                        sar_reasoning=d.get("sar_reasoning", ""),
                                        flagged_items=list(d.get("flagged_items", [])),
                                        risk_score=d.get("risk_score"))
                    ctx["screening"] = tb.screen_counterparties()
                    return decision, ctx
                if tu.name not in {t["name"] for t in TOOL_SPECS}:
                    out = {"error": f"unknown tool {tu.name}"}
                else:
                    out = audit.tool_call(tb, tu.name, dict(tu.input or {}))
                results.append({"type": "tool_result", "tool_use_id": tu.id,
                                "content": json.dumps(out, default=str)[:60_000]})
            messages.append({"role": "user", "content": results})

        # Ran out of turns: fail safe to a human
        audit.log("llm_turn_limit", max_turns=self.max_turns)
        return Decision(alert_id=tb.alert["alert_id"], disposition="ESCALATE_L2", typologies=["none"], confidence=0.0,
                        risk_indicators=["Agent did not complete within turn limit"], evidence_txn_ids=[],
                        rationale="Fail-safe escalation: turn limit reached", narrative="",
                        agent=self.name, model=self.model), ctx
