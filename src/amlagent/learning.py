"""Learning from analyst decisions.

Analysts record a decision on every case they review (account: close / restrict / no action / request
documents; SAR: file / don't file; and a reason). Those decisions are expert labels. This module turns them
into two kinds of learning:

1. A learned scorer. A logistic regression over the investigation indicators (the same numbers the agents
   see), trained only on cases an analyst has decided. It learns which signals analysts actually act on, for
   example that a withdrawn payer dispute is not evidence and a live shared device is. It is always evaluated
   on alerts no analyst has reviewed yet, so the score is never marked against its own training data.
2. Feedback for the LLM agent. Cases where the analyst disagreed with the agent, with the analyst's reason,
   are given to the Claude agent as lessons (see claude_agent.py).

Run:
    python -m amlagent.learning simulate --share 0.5   # demo: an "analyst" decides half the queue using ground truth
    python -m amlagent.learning train
    python -m amlagent.evaluate --agent learned        # scored on the alerts nobody reviewed
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from . import config as C
from .agents.baseline import BaselineAgent
from .audit import AuditLog
from .schema import Decision
from .tools import CaseData, Toolbox

REVIEWS = C.AUDIT_DIR / "reviews.jsonl"
MODEL_PATH = C.ROOT / "models" / "learned_model.json"
SUSPICIOUS_DECISIONS = {"CLOSE_ACCOUNT", "RESTRICT"}


# ------------------------------------------------------------------------------------------------ reviews
def load_reviews() -> pd.DataFrame:
    if not REVIEWS.exists():
        return pd.DataFrame(columns=["alert_id", "account_decision", "sar_decision", "reason", "agent_disposition", "ts"])
    rows = [json.loads(line) for line in REVIEWS.read_text(encoding="utf-8").splitlines() if line.strip()]
    df = pd.DataFrame(rows)
    return df.sort_values("ts").groupby("alert_id").tail(1)          # latest decision per alert wins


def review_label(r) -> int | None:
    """1 = the analyst treated the case as suspicious, 0 = not, None = can't tell."""
    if r.get("sar_decision") == "FILE_SAR" or r.get("account_decision") in SUSPICIOUS_DECISIONS:
        return 1
    if r.get("sar_decision") == "NO_SAR" and r.get("account_decision") in ("NO_ACTION", "REQUEST_DOCUMENTS"):
        return 0
    legacy = str(r.get("decision", ""))                             # v1/v2 reviewer form
    if legacy in ("Override: escalate", "Override: SAR"):
        return 1
    if legacy == "Override: close":
        return 0
    if legacy == "Agree":
        return int(str(r.get("agent_disposition")) != "CLOSE_FALSE_POSITIVE")
    return None


def record_review(alert_id: str, account_decision: str, sar_decision: str, reason: str, reviewer: str,
                  agent_decision: Decision | None = None) -> dict:
    rec = {"ts": datetime.now(timezone.utc).isoformat(), "alert_id": alert_id, "reviewer": reviewer,
           "account_decision": account_decision, "sar_decision": sar_decision, "reason": reason,
           "agent": agent_decision.agent if agent_decision else "", "agent_disposition": agent_decision.disposition if agent_decision else "",
           "agent_account_action": agent_decision.account_action if agent_decision else ""}
    REVIEWS.parent.mkdir(parents=True, exist_ok=True)
    with REVIEWS.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    return rec


def simulate(share: float = 0.5, seed: int = 7) -> int:
    """Demo only: pretend an analyst worked through part of the queue, deciding each case correctly (from
    ground truth) and disagreeing with the agent where it was wrong. Replaces earlier simulated decisions."""
    truth = pd.read_csv(C.DATA_DIR / "ground_truth.csv")
    data = CaseData.load()
    agent = BaselineAgent(level=3)
    ids = truth.sample(frac=share, random_state=seed).alert_id.tolist()
    kept = []
    if REVIEWS.exists():
        kept = [line for line in REVIEWS.read_text(encoding="utf-8").splitlines()
                if line.strip() and json.loads(line).get("reviewer") != "simulated analyst"]
    REVIEWS.parent.mkdir(parents=True, exist_ok=True)
    REVIEWS.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
    t = truth.set_index("alert_id")
    for aid in ids:
        d, ctx = agent.investigate(Toolbox(data, aid), AuditLog("sim", aid, enabled=False))
        g = t.loc[aid]
        sus = bool(g.is_suspicious)
        agreed = d.is_escalation == sus
        why = "Agree with the agent." if agreed else _analyst_reason(g, ctx["indicators"], sus)
        record_review(aid, g.expected_action if sus else "NO_ACTION", "FILE_SAR" if sus else "NO_SAR",
                      why, "simulated analyst", d)
    return len(ids)


def _analyst_reason(g, ind: dict, suspicious: bool) -> str:
    """How an analyst would explain overruling the agent (used for the simulated demo decisions)."""
    dsp, net = ind.get("disputes", {}), ind.get("network", {})
    if not suspicious:
        if dsp.get("victim_claims") and not dsp.get("victim_claims_not_withdrawn"):
            return "The payer withdrew the dispute, so it is not evidence of victim funds. Activity fits the profile."
        if net.get("adverse_links_medium_or_strong") and not net.get("adverse_device_used_concurrently"):
            return ("The closed account used that device months before this customer did (second-hand phone); "
                    "no overlap in time, so not a live link.")
        return {"shared_expenses": "Payers are flatmates and friends splitting rent and a holiday (several share the home IP); "
                                   "the references explain it and the money went to the landlord and Airbnb.",
                "own_account_sweep": "Funds moved to the customer's own investment account.",
                "one_off_large_credit": "Documented sale proceeds from a solicitor, retained in the account.",
                "sanctions_false_hit": "Similar name but a different company in a different country.",
                }.get(g.scenario, "Activity is explained and consistent with the customer's profile.")
    return {"sanctions_transliteration": "Payee is a transliterated/abbreviated form of a sanctioned company in the same "
                                         "country, and the account was accessed from a sanctioned country.",
            "sanctions_exposure": "Payee is an abbreviated form of a sanctioned party in the same country.",
            "mule_cash_out": "Recruited mule: a new device took over the account, the password and phone were changed, "
                             "and funds from many payers were passed straight on.",
            "money_mule": "Money mule: many unrelated payers and funds passed straight on.",
            }.get(g.scenario, "Activity is not explained by the profile; grounds for suspicion.")


# ------------------------------------------------------------------------------------------------ features
def features(ind: dict) -> dict[str, float]:
    """Flatten the numeric investigation indicators into model features."""
    f = {}
    skip = ("ids", "countries", "window", "declared_ties", "types", "typologies", "new_devices_window")
    for section, vals in ind.items():
        if not isinstance(vals, dict):
            continue
        for k, v in vals.items():
            if any(x in k for x in skip) and not isinstance(v, (int, float, bool)):
                continue
            if isinstance(v, bool):
                f[f"{section}.{k}"] = float(v)
            elif isinstance(v, (int, float)):
                f[f"{section}.{k}"] = float(v)
            elif isinstance(v, list):
                f[f"{section}.{k}_count"] = float(len(v))
    exp = max(float(ind.get("expected_monthly_inflow_eur") or 1), 1.0)
    for k in list(f):                                   # money amounts relative to expected activity
        if k.endswith("_eur"):
            f[k] = f[k] / exp
    f["profile.is_business"] = float(ind.get("customer_type") == "Business")
    return f


def feature_matrix(data: CaseData, alert_ids: list[str]) -> pd.DataFrame:
    rows = {aid: features(Toolbox(data, aid).compute_risk_indicators()) for aid in alert_ids}
    return pd.DataFrame.from_dict(rows, orient="index").fillna(0.0)


# ------------------------------------------------------------------------------------------------ model
def train(C_reg: float = 0.5) -> dict:
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import cross_val_score

    data = CaseData.load()
    rv = load_reviews()
    rv["label"] = [review_label(r) for r in rv.to_dict("records")]
    rv = rv.dropna(subset=["label"])
    if rv.label.nunique() < 2 or len(rv) < 20:
        raise SystemExit(f"Need at least 20 analyst decisions with both outcomes; have {len(rv)}.")
    X = feature_matrix(data, rv.alert_id.tolist())
    X = X.loc[:, X.std() > 0]
    y = rv.label.astype(int).to_numpy()
    mu, sd = X.mean(), X.std().replace(0, 1)
    Z = (X - mu) / sd
    clf = LogisticRegression(C=C_reg, class_weight="balanced", max_iter=2000)
    cv = cross_val_score(clf, Z, y, cv=5, scoring="roc_auc")
    clf.fit(Z, y)
    model = {"features": X.columns.tolist(), "mean": mu.round(6).tolist(), "std": sd.round(6).tolist(),
             "coef": clf.coef_[0].round(6).tolist(), "intercept": float(clf.intercept_[0]),
             "trained_on_alerts": rv.alert_id.tolist(), "n_train": int(len(rv)), "n_suspicious": int(y.sum()),
             "cv_roc_auc": round(float(cv.mean()), 3), "trained_at": datetime.now(timezone.utc).isoformat()}
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    MODEL_PATH.write_text(json.dumps(model, indent=1), encoding="utf-8")
    return model


def load_model() -> dict | None:
    return json.loads(MODEL_PATH.read_text(encoding="utf-8")) if MODEL_PATH.exists() else None


def score(model: dict, ind: dict) -> float:
    f = features(ind)
    x = np.array([f.get(k, 0.0) for k in model["features"]])
    z = (x - np.array(model["mean"])) / np.array(model["std"])
    return float(1 / (1 + np.exp(-(z @ np.array(model["coef"]) + model["intercept"]))))


def top_weights(model: dict, n: int = 12) -> pd.DataFrame:
    w = pd.DataFrame({"feature": model["features"], "weight": model["coef"]})
    return w.reindex(w.weight.abs().sort_values(ascending=False).index).head(n)


def holdout_ids(model: dict, data: CaseData) -> list[str]:
    seen = set(model["trained_on_alerts"])
    return [a for a in data.alerts.alert_id if a not in seen]


class LearnedAgent:
    """Rules v3 gather and explain the evidence; the learned scorer (trained on analyst decisions) decides how
    suspicious the case is. Where they disagree, the case goes to a human with both views stated."""
    model_name = "logistic-regression-on-analyst-decisions"

    def __init__(self, model: dict | None = None):
        self.m = model or load_model()
        if self.m is None:
            raise SystemExit("No learned model. Run: python -m amlagent.learning train")
        self.base = BaselineAgent(level=3)
        self.name = "learned-v1"
        self.model = f"{self.model_name} (n={self.m['n_train']})"

    def investigate(self, tb: Toolbox, audit: AuditLog):
        d, ctx = self.base.investigate(tb, audit)
        p = score(self.m, ctx["indicators"])
        audit.log("learned_score", probability=round(p, 4), n_train=self.m["n_train"])
        d.agent, d.model, d.risk_score = self.name, self.model, round(p, 4)
        note = f"Learned score {p:.2f} (from {self.m['n_train']} analyst decisions)"
        if d.is_escalation and p < 0.2:
            # rules escalated, analysts' pattern says benign: don't auto-anything, send to a human with both views
            d.disposition, d.typologies, d.confidence = "CLOSE_FALSE_POSITIVE", ["none"], round(min(1 - p, 0.8), 2)
            d.account_action, d.documents_requested = "NO_ACTION", []
            d.rationale = f"{note} disagrees with the rules' escalation ({'; '.join(d.risk_indicators)[:300]}). " \
                          "Analysts have closed similar cases; confirm before closing."
            d.sar_reasoning = "No SAR recommended by the learned score; an analyst confirms because the rules disagreed."
            d.evidence_txn_ids, d.flagged_items = [], []
            d.narrative = f"Alert {d.alert_id} on {tb.customer_id}: {d.rationale}"
        elif not d.is_escalation and p > 0.8:
            d.disposition, d.confidence = "ESCALATE_L2", round(p, 2)
            d.typologies = ["money_mule"] if tb.alert["rule_id"] == "TM-FAN-03" else \
                ["pass_through"] if tb.alert["rule_id"] == "TM-RMF-02" else ["sanctions_exposure"] \
                if tb.alert["rule_id"] in ("SCR-PAY-05", "TM-HVW-07") else ["none"]
            ind = ctx["indicators"]
            ev = (ind.get("access", {}).get("evidence_login_ids", [])[:3] + ind.get("activity", {}).get("risky_event_ids", [])[:3]
                  + ind.get("network", {}).get("adverse_linked_ids", [])[:3]) or ind["turnover"]["outflow_external_txn_ids"][:5]
            d.evidence_txn_ids = ev
            d.risk_indicators = [f"{note}: cases with this profile were treated as suspicious by analysts, "
                                 "although no single rule fired"]
            d.account_action, d.documents_requested = "REQUEST_DOCUMENTS", self.base.DOCS.get(d.typologies[0], self.base.DOCS["money_mule"])
            d.rationale = d.risk_indicators[0]
            d.sar_reasoning = "No SAR yet: escalated on the learned score; L2 decides whether grounds for suspicion exist."
            d.narrative = f"Alert {d.alert_id} on {tb.customer_id} escalated. {d.rationale}. Evidence: {', '.join(ev)}."
        else:
            d.rationale = f"{d.rationale} {note} agrees."
        return d, ctx


# ------------------------------------------------------------------------------------------------ LLM feedback
def feedback_examples(limit: int = 8) -> list[dict]:
    """Recent cases where the analyst overrode the agent, with the analyst's reason: lessons for the LLM."""
    rv = load_reviews()
    if rv.empty:
        return []
    rv["label"] = [review_label(r) for r in rv.to_dict("records")]
    rv["agent_sus"] = rv.agent_disposition.fillna("").ne("CLOSE_FALSE_POSITIVE") & rv.agent_disposition.fillna("").ne("")
    dis = rv[rv.label.notna() & (rv.label.astype(float) != rv.agent_sus.astype(float))]
    dis = dis[dis.reason.fillna("").str.len() > 0].tail(limit)
    return [{"alert_id": r.alert_id, "agent_said": r.agent_disposition, "analyst_decided": f"{r.account_decision}/{r.sar_decision}",
             "analyst_reason": r.reason} for r in dis.itertuples()]


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("simulate")
    s.add_argument("--share", type=float, default=0.5)
    sub.add_parser("train")
    a = ap.parse_args()
    if a.cmd == "simulate":
        print(f"Recorded {simulate(a.share)} simulated analyst decisions in {REVIEWS}")
    else:
        m = train()
        print(f"Trained on {m['n_train']} analyst decisions ({m['n_suspicious']} suspicious); "
              f"5-fold CV ROC AUC {m['cv_roc_auc']}. Saved to {MODEL_PATH}")
        print(top_weights(m).to_string(index=False))


if __name__ == "__main__":
    main()
