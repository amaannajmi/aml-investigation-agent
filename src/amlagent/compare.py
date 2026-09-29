"""Side-by-side comparison of every evaluated agent (reports/metrics_*.json).

Run:  python -m amlagent.compare
"""
from __future__ import annotations

import json

from . import config as C

ORDER = ["baseline_v1", "baseline_v2", "baseline", "baseline_holdout", "learned", "claude"]
LABEL = {"baseline_v1": "Rules v1 (transactions)", "baseline_v2": "Rules v2 (+ logins, network, disputes)",
         "baseline": "Rules v3 (+ account activity)", "baseline_holdout": "Rules v3, held-out alerts only",
         "learned": "Learned from analyst decisions (held-out alerts)",
         "claude": "Claude agent (all tools)"}
HEADLINE = [("accuracy", "Accuracy"), ("precision", "Precision"), ("f1", "F1"), ("roc_auc", "ROC AUC"),
            ("action_accuracy", "Account-action accuracy"), ("harmful_action_rate_benign", "Restrict/close on benign customers"),
            ("model_recall", "Model recall"), ("system_recall_after_guardrails", "System recall after guardrails"),
            ("fp_clearance_rate", "False-positive clearance"), ("auto_close_rate_benign", "Auto-close rate (benign)"),
            ("escalation_precision", "Escalation precision"), ("typology_accuracy_on_caught", "Typology accuracy"),
            ("guardrail_violation_rate", "Guardrail violation rate")]


def load() -> dict[str, dict]:
    out = {}
    for tag in ORDER:
        p = C.REPORTS_DIR / f"metrics_{tag}.json"
        if p.exists():
            out[tag] = json.loads(p.read_text(encoding="utf-8"))
    return out


def pct(x, key=""):
    if x is None:
        return "n/a"
    return f"{x:.3f}" if key == "roc_auc" else f"{x:.1%}"


def render(ms: dict[str, dict]) -> str:
    tags = list(ms)
    L = ["# Agent comparison", "",
         "Rules v1-v3 and Claude are scored on all alerts. The learned agent is scored only on alerts no analyst decided "
         "(it never sees its own training cases), so compare it with the rules on the same held-out alerts.", "",
         "| Metric | " + " | ".join(LABEL[t] for t in tags) + " |", "|---|" + "---|" * len(tags),
         "| Alerts evaluated | " + " | ".join(str(ms[t]["alerts"]) for t in tags) + " |"]
    L += [f"| {name} | " + " | ".join(pct(ms[t].get(k), k) for t in tags) + " |" for k, name in HEADLINE]
    L += ["", "## Recall by scenario", "", "| Scenario | " + " | ".join(LABEL[t] for t in tags) + " |", "|---|" + "---|" * len(tags)]
    for sc in ms[tags[0]]["recall_by_scenario"]:
        L.append(f"| {sc} | " + " | ".join(pct(ms[t]["recall_by_scenario"].get(sc, {}).get("rate")) for t in tags) + " |")
    L += ["", "## False-positive clearance by scenario", "", "| Scenario | " + " | ".join(LABEL[t] for t in tags) + " |",
          "|---|" + "---|" * len(tags)]
    for sc in ms[tags[0]]["clearance_by_scenario"]:
        L.append(f"| {sc} | " + " | ".join(pct(ms[t]["clearance_by_scenario"].get(sc, {}).get("rate")) for t in tags) + " |")
    return "\n".join(L) + "\n"


def main():
    ms = load()
    if not ms:
        raise SystemExit("No metrics found. Run python -m amlagent.evaluate first.")
    text = render(ms)
    (C.REPORTS_DIR / "comparison.md").write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
