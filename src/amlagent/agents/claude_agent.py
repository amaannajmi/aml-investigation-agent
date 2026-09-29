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
from ..schema import DISPOSITIONS, TYPOLOGIES, Decision
from ..tools import TOOL_SPECS, Toolbox

SYSTEM_PROMPT = """You are an L1 AML investigator at a regulated payments firm. You investigate one transaction-monitoring alert at a time and recommend a disposition to a human analyst. You never file reports or close cases yourself.

Investigation procedure:
1. Read the alert and the customer's KYC profile. Note the expected activity.
2. Pull the behavioural indicators and the transactions for the alert window. Compare with the customer's OWN history (period='history'): a pattern that has always existed and fits the profile is weaker evidence than a sudden change.
3. Screen counterparties. A potential sanctions match where the country also matches must never be closed.
4. Actively look for a legitimate explanation (own-account transfers, documented sale proceeds, a business model that explains many small payers, a longstanding counterparty). Only close when you have found one, not merely because red flags are absent.
5. Decide:
   - CLOSE_FALSE_POSITIVE: a specific, evidenced benign explanation exists.
   - ESCALATE_L2: red flags present but not conclusive, or you cannot rule out suspicion.
   - RECOMMEND_SAR: clear indicators of a known typology (structuring, pass_through, money_mule, high_risk_jurisdiction, sanctions_exposure).
6. Missing a genuinely suspicious case is far more costly than escalating a benign one. When in doubt, escalate.

Evidence rules (these are checked automatically and violations are routed to a human):
- Cite only transaction IDs that appeared in tool results. Never invent IDs, amounts or dates.
- Every amount in the narrative must come from a transaction or a sum of cited transactions.
- Confidence is your calibrated probability that the disposition is correct.

The narrative should read like the grounds-for-suspicion section of a SAR: who, what, when, how much, why it is unusual for this customer, and which typology it indicates. For closures, state the benign explanation and the evidence for it. Be concise."""

SUBMIT_TOOL = {
    "name": "submit_decision",
    "description": "Submit your final recommendation. Call exactly once, when the investigation is complete.",
    "input_schema": {
        "type": "object",
        "required": ["disposition", "typologies", "confidence", "risk_indicators", "evidence_txn_ids", "rationale", "narrative"],
        "properties": {
            "disposition": {"type": "string", "enum": list(DISPOSITIONS)},
            "typologies": {"type": "array", "items": {"type": "string", "enum": list(TYPOLOGIES)}},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "risk_indicators": {"type": "array", "items": {"type": "string"}},
            "evidence_txn_ids": {"type": "array", "items": {"type": "string"}},
            "rationale": {"type": "string", "description": "Short internal reasoning for the reviewer."},
            "narrative": {"type": "string", "description": "SAR-style grounds for suspicion, or the evidenced benign explanation."},
        },
    },
}


class ClaudeAgent:
    name = "claude-tool-use"

    def __init__(self, model: str | None = None, client=None, max_turns: int = C.LLM_MAX_TURNS):
        self.model = model or C.LLM_MODEL
        self.max_turns = max_turns
        if client is None:
            import anthropic
            client = anthropic.Anthropic()
        self.client = client

    def investigate(self, tb: Toolbox, audit: AuditLog) -> tuple[Decision, dict]:
        messages = [{"role": "user", "content": f"Investigate alert {tb.alert['alert_id']} and submit your decision."}]
        tools = TOOL_SPECS + [SUBMIT_TOOL]
        ctx: dict = {"usage": {"input_tokens": 0, "output_tokens": 0}, "turns": 0}
        audit.log("llm_start", model=self.model)

        for turn in range(self.max_turns):
            resp = self.client.messages.create(model=self.model, max_tokens=4096, system=SYSTEM_PROMPT,
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
                                        agent=self.name, model=self.model)
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
