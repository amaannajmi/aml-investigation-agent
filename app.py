"""Reviewer workbench: the human-in-the-loop screen for agent-prepared cases.

Run:  streamlit run app.py
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent / "src"))

from amlagent import config as C  # noqa: E402
from amlagent.pipeline import investigate, make_agent  # noqa: E402
from amlagent.tools import CaseData, Toolbox  # noqa: E402

st.set_page_config(page_title="AML Investigation Agent", layout="wide")

STATUS_STYLE = {
    "SAR_PENDING_MLRO_APPROVAL": ("🔴", "SAR recommended, awaiting MLRO approval"),
    "HUMAN_REVIEW_REQUIRED": ("🟠", "Analyst review required"),
    "AUTO_CLOSE_ELIGIBLE": ("🟢", "Eligible for auto-close (sampled QA)"),
}
LABELS = {"RECOMMEND_SAR": "Recommend SAR", "ESCALATE_L2": "Escalate to L2", "CLOSE_FALSE_POSITIVE": "Close as false positive"}
REVIEWS = C.AUDIT_DIR / "reviews.jsonl"


@st.cache_resource
def load_data():
    if not (C.DATA_DIR / "alerts.csv").exists():
        from amlagent.data_gen import generate
        C.DATA_DIR.mkdir(exist_ok=True)
        for name, df in generate().items():
            df.to_csv(C.DATA_DIR / f"{name}.csv", index=False)
    return CaseData.load(), pd.read_csv(C.DATA_DIR / "ground_truth.csv")


data, truth = load_data()

st.sidebar.title("AML Investigation Agent")
page = st.sidebar.radio("View", ["Case review", "Evaluation"], label_visibility="collapsed")
has_key = bool(os.getenv("ANTHROPIC_API_KEY"))
agent_kind = st.sidebar.selectbox("Agent", ["baseline", "claude"] if has_key else ["baseline"],
                                  help="The Claude agent needs ANTHROPIC_API_KEY in the environment.")
show_truth = st.sidebar.toggle("Show ground truth (evaluation only)", value=False)
st.sidebar.caption("Synthetic data. Fictional customers and sanctions list. The agent recommends; people decide.")

# ------------------------------------------------------------------------------------------ case review
if page == "Case review":
    alerts = data.alerts.merge(truth, on="alert_id") if show_truth else data.alerts
    label = lambda r: f"{r.alert_id} · {r.rule_name}" + (f" · [{r.scenario}]" if show_truth else "")
    options = {label(r): r.alert_id for r in alerts.itertuples()}
    aid = options[st.selectbox("Alert queue", list(options))]
    tb = Toolbox(data, aid)
    prof = tb.get_customer_profile()

    c1, c2 = st.columns([2, 3])
    with c1:
        st.subheader(f"{aid} · {tb.alert['rule_name']}")
        st.caption(tb.alert["rule_description"])
        st.markdown(f"**{prof['customer_name']}** ({prof['customer_id']}) · {prof['customer_type']} · {prof['occupation']}  \n"
                    f"Expected monthly inflow: EUR {prof['expected_monthly_inflow_eur']:,.0f} · Risk rating: {prof['risk_rating']} · "
                    f"Onboarded {prof['onboarding_date']}")
        if show_truth:
            t = truth.set_index("alert_id").loc[aid]
            st.info(f"Ground truth: {'SUSPICIOUS' if t.is_suspicious else 'benign'} · {t.scenario} ({t.variant})")
        run = st.button("Run investigation", type="primary")

    key = f"result_{aid}_{agent_kind}"
    if run:
        with st.spinner("Agent investigating…"):
            st.session_state[key] = investigate(aid, make_agent(agent_kind), data, run_id="app", write_audit=True)
    res = st.session_state.get(key)

    if res:
        d, g = res["decision"], res["guardrails"]
        icon, text = STATUS_STYLE[g.final_status]
        with c2:
            st.subheader(f"{icon} {LABELS[d.disposition]}")
            m1, m2, m3 = st.columns(3)
            m1.metric("Confidence", f"{d.confidence:.0%}")
            m2.metric("Typology", d.typologies[0].replace("_", " ") + (f" +{len(d.typologies) - 1}" if len(d.typologies) > 1 else ""),
                      help=", ".join(d.typologies))
            m3.metric("Evidence cited", len(d.evidence_txn_ids))
            st.markdown(f"**Routing:** {text}")
            for o in g.overrides:
                st.warning(o)
            st.markdown("**Risk indicators**")
            for i in d.risk_indicators or ["None identified"]:
                st.markdown(f"- {i}")

        st.markdown("#### Draft narrative")
        st.text_area("narrative", d.narrative, height=170, label_visibility="collapsed")
        st.caption(f"Agent rationale: {d.rationale}")

        g1, g2 = st.columns([2, 3])
        with g1:
            st.markdown("#### Guardrail checks")
            for c in g.checks:
                st.markdown(f"{'✅' if c['passed'] else '❌'} `{c['name']}` {c['detail']}")
        with g2:
            st.markdown("#### Reviewer decision")
            with st.form(f"review_{aid}"):
                choice = st.radio("Decision", ["Agree", "Override: close", "Override: escalate", "Override: SAR"], horizontal=True)
                reason = st.text_input("Reason (required for overrides)")
                reviewer = st.text_input("Reviewer", value="analyst")
                if st.form_submit_button("Record decision"):
                    if choice != "Agree" and not reason.strip():
                        st.error("An override needs a reason: it is the feedback that improves the agent.")
                    else:
                        REVIEWS.parent.mkdir(exist_ok=True)
                        with REVIEWS.open("a") as f:
                            f.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), "alert_id": aid,
                                                "agent": d.agent, "agent_disposition": d.disposition,
                                                "reviewer": reviewer, "decision": choice, "reason": reason}) + "\n")
                        st.success("Recorded to the audit trail.")

        st.markdown("#### Transactions (alert window + history)")
        tx = pd.DataFrame(tb.get_transactions(period="all", limit=400)["transactions"])
        tx.insert(0, "cited", tx.txn_id.isin(d.evidence_txn_ids).map({True: "●", False: ""}))
        only = st.toggle("Only cited transactions", value=bool(d.evidence_txn_ids))
        st.dataframe(tx[tx.cited == "●"] if only else tx, hide_index=True, width="stretch")

        with st.expander("Audit trail for this run"):
            st.caption(res["audit_path"])
            st.json(res["audit_events"], expanded=False)
    else:
        with c2:
            st.info("Run the investigation to see the agent's recommendation, evidence and guardrail checks.")

# ------------------------------------------------------------------------------------------ evaluation
else:
    st.title("Evaluation")
    reports = sorted(C.REPORTS_DIR.glob("metrics_*.json"))
    if not reports:
        st.warning("No evaluation yet. Run `python -m amlagent.evaluate --agent baseline`.")
    for p in reports:
        tag = p.stem.removeprefix("metrics_")
        m = json.loads(p.read_text())
        st.subheader(f"Agent: {tag}")
        cols = st.columns(5)
        cols[0].metric("Model recall", f"{m['model_recall']:.0%}", help="Suspicious alerts escalated by the agent")
        cols[1].metric("System recall", f"{m['system_recall_after_guardrails']:.0%}", help="Suspicious alerts not auto-closed after guardrails")
        cols[2].metric("FP clearance", f"{m['fp_clearance_rate']:.0%}")
        cols[3].metric("Auto-closed", f"{m['auto_close_rate_benign']:.0%}")
        cols[4].metric("Analyst hours saved", f"{m['workload']['hours_saved_pct']:.0%}", help="Illustrative assumptions, see report")
        a, b = st.columns(2)
        a.markdown("**Recall by scenario**")
        a.bar_chart(pd.Series({k: v["rate"] for k, v in m["recall_by_scenario"].items()}), horizontal=True)
        b.markdown("**False-positive clearance by scenario**")
        b.bar_chart(pd.Series({k: v["rate"] for k, v in m["clearance_by_scenario"].items()}), horizontal=True)
        rep = C.REPORTS_DIR / f"eval_report_{tag}.md"
        if rep.exists():
            with st.expander("Full report"):
                st.markdown(rep.read_text())
