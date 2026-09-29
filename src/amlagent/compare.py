"""Side-by-side comparison of every evaluated agent (reports/metrics_*.json).

Run:  python -m amlagent.compare
"""
from __future__ import annotations

import json

from . import config as C

ORDER = ["baseline_v1", "baseline", "claude"]
LABEL = {"baseline_v1": "Rules v1 (transactions)", "baseline": "Rules v2 (+ identity, network, disputes)",
         "claude": "Claude agent (all tools)"}
HEADLINE = [("model_recall", "Model recall"), ("system_recall_after_guardrails", "System recall after guardrails"),
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


def pct(x):
    return "n/a" if x is None else f"{x:.1%}"


def render(ms: dict[str, dict]) -> str:
    tags = list(ms)
    L = ["# Agent comparison", "", "| Metric | " + " | ".join(LABEL[t] for t in tags) + " |",
         "|---|" + "---|" * len(tags)]
    L += [f"| {name} | " + " | ".join(pct(ms[t].get(k)) for t in tags) + " |" for k, name in HEADLINE]
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
