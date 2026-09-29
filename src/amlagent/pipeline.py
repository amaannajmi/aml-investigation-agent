"""Run one alert end to end: agent -> guardrails -> audit record."""
from __future__ import annotations

import time
from datetime import datetime, timezone

from .audit import AuditLog
from .guardrails import apply_guardrails
from .tools import CaseData, Toolbox


def make_agent(kind: str, **kw):
    if kind == "baseline":
        from .agents.baseline import BaselineAgent
        return BaselineAgent()
    if kind == "claude":
        from .agents.claude_agent import ClaudeAgent
        return ClaudeAgent(**kw)
    raise ValueError(f"unknown agent {kind!r}")


def new_run_id(agent_name: str) -> str:
    return f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S}_{agent_name}"


def investigate(alert_id: str, agent, data: CaseData, run_id: str, write_audit: bool = True) -> dict:
    tb = Toolbox(data, alert_id)
    audit = AuditLog(run_id, alert_id, enabled=write_audit)
    audit.log("case_start", alert_id=alert_id, customer_id=tb.customer_id, agent=agent.name, model=agent.model)
    t0 = time.perf_counter()
    try:
        decision, ctx = agent.investigate(tb, audit)
    except Exception as e:
        audit.log("agent_error", error=f"{type(e).__name__}: {e}")
        raise
    audit.log("decision", **decision.to_dict())
    gr = apply_guardrails(decision, tb, ctx.get("screening"))
    audit.log("guardrails", **gr.to_dict())
    elapsed = time.perf_counter() - t0
    audit.log("case_end", final_status=gr.final_status, seconds=round(elapsed, 3))
    return {"decision": decision, "guardrails": gr, "context": ctx, "seconds": elapsed,
            "audit_path": str(audit.path) if write_audit else None, "audit_events": audit.events}
