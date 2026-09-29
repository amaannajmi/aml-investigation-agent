"""Evaluation harness: run an agent over the labelled alert queue and score it.

Metrics are chosen for how an AFC function actually judges an investigation tool:

* Recall on suspicious alerts (the regulatory risk: a missed SAR)
* System recall: suspicious alerts that were NOT auto-closed after guardrails
  (the human safety net catches what the model misses)
* False-positive clearance and auto-close rate (the efficiency buyers pay for)
* Typology accuracy, grounding violations, narrative structure checks
* Per-scenario and per-difficulty breakdowns, and a list of every miss

Run:  python -m amlagent.evaluate --agent baseline
      python -m amlagent.evaluate --agent claude --limit 40   (needs ANTHROPIC_API_KEY)
"""
from __future__ import annotations

import argparse
import json
import re

import pandas as pd

from . import config as C
from .pipeline import investigate, make_agent, new_run_id
from .schema import AUTO_CLOSE_ELIGIBLE
from .tools import CaseData


def narrative_checks(d, prof_id: str) -> dict:
    """Automated structural checks on the narrative. A human QA rubric (docs/) covers quality."""
    n = d.narrative or ""
    words = len(n.split())
    if not d.is_escalation:
        return {"narrative_ok": bool(n) and prof_id in n}
    checks = {
        "names_subject": prof_id in n,
        "cites_transactions": bool(re.search(r"TXN-\d+", n)),
        "states_amounts": bool(re.search(r"(EUR|€)\s?[\d,]+", n)),
        "states_dates": bool(re.search(r"\d{4}-\d{2}-\d{2}", n)),
        "names_typology": any(t.replace("_", " ") in n.lower() or t in n.lower() for t in d.typologies if t != "none"),
        "length_ok": 60 <= words <= 450,
    }
    checks["narrative_ok"] = all(checks.values())
    return checks


def run(agent_kind: str, limit: int | None = None, write_audit: bool = True, alert_ids: list[str] | None = None):
    data = CaseData.load()
    truth = pd.read_csv(C.DATA_DIR / "ground_truth.csv")
    agent = make_agent(agent_kind)
    run_id = new_run_id(agent.name)
    ids = alert_ids or data.alerts.alert_id.tolist()
    if limit:
        ids = ids[:limit]
    rows = []
    for i, aid in enumerate(ids, 1):
        r = investigate(aid, agent, data, run_id, write_audit)
        d, g = r["decision"], r["guardrails"]
        rows.append({
            "alert_id": aid, "customer_id": data.alerts.set_index("alert_id").at[aid, "customer_id"],
            "disposition": d.disposition, "final_disposition": g.final_disposition, "final_status": g.final_status,
            "typologies": ",".join(d.typologies), "confidence": d.confidence,
            "n_evidence": len(d.evidence_txn_ids), "guardrail_violations": ";".join(c["name"] for c in g.violations), "guardrail_detail": " | ".join(f"{c['name']}: {c['detail']}" for c in g.violations),
            "overrides": " | ".join(g.overrides), "seconds": round(r["seconds"], 3),
            "input_tokens": r["context"].get("usage", {}).get("input_tokens"),
            "output_tokens": r["context"].get("usage", {}).get("output_tokens"),
            **{f"nq_{k}": v for k, v in narrative_checks(d, data.alerts.set_index("alert_id").at[aid, "customer_id"]).items()},
            "rationale": d.rationale, "narrative": d.narrative,
        })
        if i % 25 == 0 or i == len(ids):
            print(f"  {i}/{len(ids)} alerts investigated")
    res = pd.DataFrame(rows).merge(truth, on="alert_id")
    metrics = compute_metrics(res)
    C.REPORTS_DIR.mkdir(exist_ok=True)
    tag = agent_kind
    res.to_csv(C.REPORTS_DIR / f"results_{tag}.csv", index=False)
    (C.REPORTS_DIR / f"metrics_{tag}.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    (C.REPORTS_DIR / f"eval_report_{tag}.md").write_text(render_report(metrics, res, agent, run_id), encoding="utf-8")
    return metrics, res, run_id


def compute_metrics(res: pd.DataFrame) -> dict:
    sus, ben = res[res.is_suspicious == 1], res[res.is_suspicious == 0]
    caught = sus.disposition != "CLOSE_FALSE_POSITIVE"
    sys_caught = sus.final_status != AUTO_CLOSE_ELIGIBLE
    closed = ben.disposition == "CLOSE_FALSE_POSITIVE"
    auto_closed = ben.final_status == AUTO_CLOSE_ELIGIBLE
    esc = res[res.disposition != "CLOSE_FALSE_POSITIVE"]
    typ_ok = sus[caught].apply(lambda r: r.typology in r.typologies.split(","), axis=1)

    n = len(res)
    manual = n * C.MANUAL_MINUTES_PER_ALERT
    auto_n = int((res.final_status == AUTO_CLOSE_ELIGIBLE).sum())
    assisted = (n - auto_n) * C.REVIEW_MINUTES_PER_AGENT_CASE + auto_n * C.QA_SAMPLE_MINUTES_PER_AUTO_CLOSE

    def by(group_col, frame, ok):
        return {k: {"n": int(len(g)), "rate": round(float(ok.loc[g.index].mean()), 3)} for k, g in frame.groupby(group_col)}

    return {
        "alerts": n, "suspicious": int(len(sus)), "benign": int(len(ben)),
        "model_recall": round(float(caught.mean()), 3),
        "system_recall_after_guardrails": round(float(sys_caught.mean()), 3),
        "missed_suspicious": int((~caught).sum()),
        "missed_after_guardrails": int((~sys_caught).sum()),
        "fp_clearance_rate": round(float(closed.mean()), 3),
        "auto_close_rate_benign": round(float(auto_closed.mean()), 3),
        "escalation_precision": round(float((esc.is_suspicious == 1).mean()), 3) if len(esc) else None,
        "sar_recommendations": int((res.disposition == "RECOMMEND_SAR").sum()),
        "typology_accuracy_on_caught": round(float(typ_ok.mean()), 3) if len(typ_ok) else None,
        "guardrail_violation_rate": round(float((res.guardrail_violations != "").mean()), 3),
        "narrative_structure_pass_rate_escalations": round(float(res.loc[res.disposition != "CLOSE_FALSE_POSITIVE", "nq_narrative_ok"].mean()), 3) if len(esc) else None,
        "recall_by_scenario": by("scenario", sus, caught),
        "recall_by_variant": by("variant", sus, caught),
        "clearance_by_scenario": by("scenario", ben, closed),
        "auto_close_by_scenario": by("scenario", ben, auto_closed),
        "workload": {"manual_hours": round(manual / 60, 1), "assisted_hours": round(assisted / 60, 1),
                     "hours_saved_pct": round(1 - assisted / manual, 3),
                     "assumptions_minutes": {"manual_per_alert": C.MANUAL_MINUTES_PER_ALERT,
                                             "review_per_agent_case": C.REVIEW_MINUTES_PER_AGENT_CASE,
                                             "qa_per_auto_close": C.QA_SAMPLE_MINUTES_PER_AUTO_CLOSE}},
        "mean_seconds_per_alert": round(float(res.seconds.mean()), 3),
    }


def _pct(x):
    return "n/a" if x is None else f"{x:.1%}"


def render_report(m: dict, res: pd.DataFrame, agent, run_id: str) -> str:
    L = [f"# Evaluation report: {agent.name}", "",
         f"Run `{run_id}` · model `{agent.model}` · {m['alerts']} alerts ({m['suspicious']} suspicious, {m['benign']} benign) · "
         f"auto-close confidence threshold {C.AUTO_CLOSE_MIN_CONFIDENCE}", "",
         "## Headline", "",
         "| Metric | Value | Why it matters |", "|---|---|---|",
         f"| Model recall (suspicious escalated) | **{_pct(m['model_recall'])}** ({m['missed_suspicious']} missed) | A missed case is the regulatory risk |",
         f"| System recall after guardrails | **{_pct(m['system_recall_after_guardrails'])}** ({m['missed_after_guardrails']} auto-closed wrongly) | What actually slips past a human |",
         f"| False-positive clearance | {_pct(m['fp_clearance_rate'])} | Benign alerts the agent recommends closing |",
         f"| Auto-close rate (benign) | {_pct(m['auto_close_rate_benign'])} | Workload actually removed |",
         f"| Escalation precision | {_pct(m['escalation_precision'])} | Share of escalations that are truly suspicious |",
         f"| Typology accuracy (caught cases) | {_pct(m['typology_accuracy_on_caught'])} | Right reason, not just right answer |",
         f"| Guardrail violation rate | {_pct(m['guardrail_violation_rate'])} | Fabricated citations, missing evidence, policy breaches |",
         f"| Narrative structure pass rate | {_pct(m['narrative_structure_pass_rate_escalations'])} | Subject, dates, amounts, IDs, typology present |",
         f"| Estimated analyst hours | {m['workload']['manual_hours']}h → {m['workload']['assisted_hours']}h "
         f"(−{_pct(m['workload']['hours_saved_pct'])}) | Illustrative; assumptions below |", "",
         "## Recall by typology and difficulty", "", "| Scenario | n | Recall |", "|---|---|---|"]
    L += [f"| {k} | {v['n']} | {_pct(v['rate'])} |" for k, v in m["recall_by_scenario"].items()]
    L += [f"| *variant: {k}* | {v['n']} | {_pct(v['rate'])} |" for k, v in m["recall_by_variant"].items()]
    L += ["", "## False-positive handling by scenario", "", "| Scenario | n | Recommended close | Auto-closed |", "|---|---|---|---|"]
    L += [f"| {k} | {v['n']} | {_pct(v['rate'])} | {_pct(m['auto_close_by_scenario'][k]['rate'])} |" for k, v in m["clearance_by_scenario"].items()]

    missed = res[(res.is_suspicious == 1) & (res.disposition == "CLOSE_FALSE_POSITIVE")]
    L += ["", f"## Missed suspicious alerts ({len(missed)})", ""]
    if len(missed):
        L += ["| Alert | Scenario | Variant | Confidence | Final status | Agent's reasoning |", "|---|---|---|---|---|---|"]
        L += [f"| {r.alert_id} | {r.scenario} | {r.variant} | {r.confidence:.2f} | {r.final_status} | {r.rationale[:160]} |"
              for r in missed.itertuples()]
    else:
        L.append("None.")
    fps = res[(res.is_suspicious == 0) & (res.disposition != "CLOSE_FALSE_POSITIVE")]
    L += ["", f"## Benign alerts escalated ({len(fps)})", ""]
    if len(fps):
        L += ["| Alert | Scenario | Variant | Disposition | Typology given |", "|---|---|---|---|---|"]
        L += [f"| {r.alert_id} | {r.scenario} | {r.variant} | {r.disposition} | {r.typologies} |" for r in fps.itertuples()]
    else:
        L.append("None.")
    viol = res[res.guardrail_violations != ""]
    L += ["", f"## Guardrail violations ({len(viol)})", ""]
    L += [f"- {r.alert_id} ({r.scenario}): {r.guardrail_detail}. Routing: {r.overrides or r.final_status}" for r in viol.itertuples()] or ["None."]
    w = m["workload"]["assumptions_minutes"]
    L += ["", "## Notes", "",
          f"- Workload estimate assumes {w['manual_per_alert']} min per manual L1 investigation, {w['review_per_agent_case']} min "
          f"to review an agent-prepared case, and {w['qa_per_auto_close']} min of sampled QA per auto-closed alert.",
          "- The dataset is synthetic with a 40% suspicious rate. Real alert queues are typically over 90% false positives, "
          "so precision here overstates production precision.",
          f"- Mean processing time {m['mean_seconds_per_alert']}s per alert."]
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", choices=["baseline", "baseline_v1", "claude"], default="baseline")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--no-audit", action="store_true")
    a = ap.parse_args()
    m, _, run_id = run(a.agent, a.limit, not a.no_audit)
    print(json.dumps({k: v for k, v in m.items() if not isinstance(v, dict)}, indent=2))
    print(f"Report: {C.REPORTS_DIR / f'eval_report_{a.agent}.md'}  |  audit: {C.AUDIT_DIR / run_id}")


if __name__ == "__main__":
    main()
