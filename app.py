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



def mark(df: pd.DataFrame, id_col: str, cited: set) -> pd.DataFrame:
    df = df.copy()
    df.insert(0, "cited", df[id_col].isin(cited).map({True: "●", False: ""}))
    return df


def network_dot(tb: Toolbox, links: dict, include_weak: bool = False) -> str:
    """Graphviz DOT: the customer, the identifiers they share, and the accounts on the other end."""
    colour = {"strong": "#d1495b", "medium": "#edae49", "weak": "#9aa5b1"}
    L = ['graph G {', 'bgcolor="transparent"; rankdir=LR; splines=true; overlap=false;',
         'node [style="filled,rounded", shape=box, fontname="Helvetica", fontsize=10, fontcolor="#111111"];',
         'edge [fontname="Helvetica", fontsize=9, fontcolor="#777777"];',
         f'"{tb.customer_id}" [label="{tb.customer_id}\\n(alerted)", fillcolor="#8fb8de", shape=ellipse];']
    pool = [l for l in links["linked_accounts"] if include_weak or l["strongest_link"] != "weak"]
    shown = sorted(pool, key=lambda l: (-{"strong": 3, "medium": 2, "weak": 1}[l["strongest_link"]], not l["is_adverse"]))[:14]
    seen = set()
    for l in shown:
        fill = "#f4a6a6" if l["is_adverse"] else "#d9e2ec"
        status = l["account_status"] + (" · SAR" if l["prior_sar"] else "")
        L.append(f'"{l["customer_id"]}" [label="{l["customer_id"]}\\n{l["customer_name"]}\\n{status}", fillcolor="{fill}", shape=ellipse];')
        for sh in [x for x in l["shared"] if include_weak or x["strength"] != "weak"]:
            node = f'{sh["identifier_type"]}:{sh["value"]}'
            if node not in seen:
                seen.add(node)
                short = sh["value"] if len(sh["value"]) <= 18 else sh["value"][:16] + "…"
                L.append(f'"{node}" [label="{sh["identifier_type"].replace("_", " ")}\\n{short}\\n{sh["accounts_sharing"]} accts", '
                         f'fillcolor="{colour[sh["strength"]]}", fontsize=9];')
                L.append(f'"{tb.customer_id}" -- "{node}" [color="{colour[sh["strength"]]}", penwidth=2];')
            L.append(f'"{node}" -- "{l["customer_id"]}" [color="{colour[sh["strength"]]}"];')
    L.append("}")
    return "\n".join(L)


def render_evidence(tb: Toolbox, cited: set) -> None:
    st.markdown("### Evidence")
    t1, t2, t3, t4 = st.tabs(["Transactions", "Identity & logins", "Linked accounts", "Disputes"])
    with t1:
        tx = mark(pd.DataFrame(tb.get_transactions(period="all", limit=400)["transactions"]), "txn_id", cited)
        only = st.toggle("Only cited transactions", value=bool(cited & set(tx.txn_id)), key=f"only_{tb.alert['alert_id']}")
        st.dataframe(tx[tx.cited == "●"] if only else tx, hide_index=True, width="stretch")
    if not tb.data.has_identity:
        for t in (t2, t3, t4):
            t.info("No identity data. Regenerate with `python -m amlagent.data_gen`.")
        return
    ind = tb.compute_risk_indicators()
    with t2:
        idp = tb.get_identity_profile()
        a, b, c, d = st.columns(4)
        a.metric("Signed up", str(idp["signup_date"]), help=f"{idp['account_age_days']} days ago")
        b.metric("Account status", idp["account_status"])
        c.metric("Sign-up country", idp["signup_country"])
        d.metric("Prior SAR", "Yes" if idp["prior_sar"] else "No")
        st.markdown(f"**Phone** {idp['phone']} · **Email** {idp['email']} · **Home city** {idp['home_city']}  \n"
                    f"**Sign-up IP** `{idp['signup_ip']}` · **Sign-up device** `{idp['signup_device_id']}`")
        acc = ind["access"]
        flags = []
        if acc["new_devices_window"]:
            flags.append(f"New device in window: {', '.join(acc['new_devices_window'])} ({acc['new_device_login_share_window']:.0%} of logins)")
        if acc["foreign_login_countries_window"]:
            flags.append(f"Foreign logins: {', '.join(acc['foreign_login_countries_window'])} ({acc['foreign_login_share_window']:.0%})")
        if acc["vpn_hosting_login_share_window"]:
            flags.append(f"Hosting/VPN logins: {acc['vpn_hosting_login_share_window']:.0%}")
        if acc["sanctioned_country_logins_window"]:
            flags.append(f"Logins from a sanctioned country: {acc['sanctioned_country_logins_window']}")
        for f in flags:
            st.warning(f)
        if not flags:
            st.success("Access pattern in the alert window is consistent with history.")
        lh = tb.get_login_history(limit=10)
        st.markdown(f"**Last 10 logins** · {lh['total_logins_90d']} logins, {lh['distinct_devices_90d']} devices, "
                    f"{lh['distinct_ips_90d']} IPs in 90 days · countries: {', '.join(lh['countries_90d'])}")
        logins = mark(pd.DataFrame(lh["logins"]), "login_id", cited)
        cols = ["cited", "login_id", "timestamp", "ip", "ip_type", "city", "country", "device_id", "visitor_id",
                "new_device_in_window", "user_agent", "success"]
        st.dataframe(logins[cols], hide_index=True, width="stretch")
        st.map(pd.DataFrame(tb.get_login_history(limit=100)["logins"])[["latitude", "longitude"]], zoom=1, height=300)
    with t3:
        links = tb.get_linked_accounts()
        net = ind["network"]
        a, b, c = st.columns(3)
        a.metric("Linked accounts", links["linked_account_count"])
        b.metric("Closed / limited / SAR", links["adverse_linked_accounts"])
        c.metric("Medium/strong links to those", net["adverse_links_medium_or_strong"])
        if links["linked_accounts"]:
            n_weak = sum(1 for l in links["linked_accounts"] if l["strongest_link"] == "weak")
            weak = st.toggle(f"Show weak IP-only links ({n_weak} accounts)", value=False, key=f"weak_{tb.alert['alert_id']}")
            if weak or n_weak < len(links["linked_accounts"]):
                st.graphviz_chart(network_dot(tb, links, include_weak=weak), width="stretch")
            else:
                st.info("Only weak IP-only links (shared VPN exits, hotspots or wider networks). Toggle to show them.")
            st.caption("Red accounts are closed, limited or previously reported. Link colour: red strong "
                       "(device, bank account, phone, email), amber medium (browser ID, small shared household IP), "
                       "grey weak (IP shared more widely). Top 14 accounts shown.")
            rows = [{"cited": "●" if l["customer_id"] in cited else "", "customer_id": l["customer_id"],
                     "name": l["customer_name"], "status": l["account_status"], "reason": l["status_reason"],
                     "prior_sar": l["prior_sar"], "strongest_link": l["strongest_link"],
                     "shared": "; ".join(f"{s['identifier_type']} ({s['strength']}, {s['accounts_sharing']} accts)" for s in l["shared"])}
                    for l in links["linked_accounts"]]
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        else:
            st.info("No linked accounts beyond suppressed high-cardinality identifiers.")
        with st.expander(f"Suppressed identifiers ({len(links['suppressed_high_cardinality_identifiers'])})"):
            st.caption(links["note"])
            st.dataframe(pd.DataFrame(links["suppressed_high_cardinality_identifiers"]), hide_index=True, width="stretch")
        inst = tb.data.instruments
        st.markdown("**Bank accounts and cards on file**")
        mine = inst[inst.customer_id == tb.customer_id].copy()
        mine["also_on_accounts"] = mine.fingerprint.map(
            lambda fp: ", ".join(sorted(tb.data.link_index.get(("bank_fingerprint", fp), set()) - {tb.customer_id})) or "-")
        st.dataframe(mine.drop(columns=["customer_id"]), hide_index=True, width="stretch")
    with t4:
        ds = tb.get_disputes()
        dd = ind["disputes"]
        a, b, c = st.columns(3)
        a.metric("Disputes", ds["count"])
        b.metric("Payer claims: unauthorised / scam", dd["victim_claims"])
        c.metric("Buyer claims", dd["buyer_claims"])
        if ds["count"]:
            st.dataframe(mark(pd.DataFrame(ds["disputes"]), "dispute_id", cited), hide_index=True, width="stretch")
        else:
            st.success("No disputes on this account.")


data, truth = load_data()

st.sidebar.title("AML Investigation Agent")
page = st.sidebar.radio("View", ["Case review", "Evaluation"], label_visibility="collapsed")
has_key = bool(os.getenv("ANTHROPIC_API_KEY"))
agent_kind = st.sidebar.selectbox("Agent", ["baseline", "baseline_v1"] + (["claude"] if has_key else []),
                                  help="baseline = rules with identity, network and dispute evidence (v2). "
                                       "baseline_v1 = transactions only. The Claude agent needs ANTHROPIC_API_KEY.")
show_truth = st.sidebar.toggle("Show ground truth (evaluation only)", value=False)
st.sidebar.caption("Synthetic data. Fictional customers and sanctions list. The agent recommends; people decide.")

# ------------------------------------------------------------------------------------------ case review
if page == "Case review":
    alerts = data.alerts.merge(truth, on="alert_id") if show_truth else data.alerts
    label = lambda r: f"{r.alert_id} · {r.rule_name}" + (f" · [{r.scenario}]" if show_truth else "")
    options = {label(r): r.alert_id for r in alerts.itertuples()}
    ids = list(options.values())
    wanted = st.query_params.get("alert")                     # deep link: ?alert=ALR-0006
    aid = options[st.selectbox("Alert queue", list(options), index=ids.index(wanted) if wanted in ids else 0)]
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
                        with REVIEWS.open("a", encoding="utf-8") as f:
                            f.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), "alert_id": aid,
                                                "agent": d.agent, "agent_disposition": d.disposition,
                                                "reviewer": reviewer, "decision": choice, "reason": reason}) + "\n")
                        st.success("Recorded to the audit trail.")

        with st.expander("Audit trail for this run"):
            st.caption(res["audit_path"])
            st.json(res["audit_events"], expanded=False)
    else:
        with c2:
            st.info("Run the investigation to see the agent's recommendation, evidence and guardrail checks. "
                    "The evidence tabs below are available now.")

    render_evidence(tb, set(res["decision"].evidence_txn_ids) if res else set())

# ------------------------------------------------------------------------------------------ evaluation
else:
    st.title("Evaluation")
    comp = C.REPORTS_DIR / "comparison.md"
    if comp.exists():
        with st.expander("Side-by-side comparison of all evaluated agents", expanded=True):
            st.markdown(comp.read_text(encoding="utf-8").split("\n", 2)[2])
    reports = sorted(C.REPORTS_DIR.glob("metrics_*.json"))
    if not reports:
        st.warning("No evaluation yet. Run `python -m amlagent.evaluate --agent baseline`.")
    for p in reports:
        tag = p.stem.removeprefix("metrics_")
        m = json.loads(p.read_text(encoding="utf-8"))
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
                st.markdown(rep.read_text(encoding="utf-8"))
