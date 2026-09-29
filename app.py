"""AML Transaction Monitoring workbench: upload data, review a case, see the agent's recommendation, decide.

Run:  streamlit run app.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent / "src"))

from amlagent import config as C  # noqa: E402
from amlagent import learning, upload  # noqa: E402
from amlagent.pipeline import investigate, make_agent  # noqa: E402
from amlagent.tools import CaseData, Toolbox  # noqa: E402

st.set_page_config(page_title="AML Transaction Monitoring", layout="wide")

STATUS_STYLE = {
    "SAR_PENDING_MLRO_APPROVAL": ("🔴", "SAR recommended, awaiting MLRO approval"),
    "HUMAN_REVIEW_REQUIRED": ("🟠", "Analyst review required"),
    "AUTO_CLOSE_ELIGIBLE": ("🟢", "Eligible for auto-close (sampled QA)"),
}
LABELS = {"RECOMMEND_SAR": "Recommend SAR", "ESCALATE_L2": "Escalate to L2", "CLOSE_FALSE_POSITIVE": "Close as false positive"}
ACTION_LABELS = {"CLOSE_ACCOUNT": "Close account (ban)", "RESTRICT": "Restrict account", "NO_ACTION": "No action (don't ban)",
                 "REQUEST_DOCUMENTS": "Request documents"}
ACTION_ICON = {"CLOSE_ACCOUNT": "⛔", "RESTRICT": "🔒", "NO_ACTION": "✅", "REQUEST_DOCUMENTS": "📄"}
AGENTS = {"baseline": "Rules v3 (all evidence)", "baseline_v2": "Rules v2 (no account activity)",
          "baseline_v1": "Rules v1 (transactions only)", "learned": "Learned from analyst decisions", "claude": "Claude agent"}


# ------------------------------------------------------------------------------------------------ data
@st.cache_resource
def load_demo():
    if not (C.DATA_DIR / "alerts.csv").exists():
        from amlagent.data_gen import generate
        C.DATA_DIR.mkdir(exist_ok=True)
        for name, df in generate().items():
            df.to_csv(C.DATA_DIR / f"{name}.csv", index=False)
    return CaseData.load(), pd.read_csv(C.DATA_DIR / "ground_truth.csv")


@st.cache_resource
def load_folder(path: str):
    p = Path(path)
    truth = pd.read_csv(p / "ground_truth.csv") if (p / "ground_truth.csv").exists() else None
    return CaseData.load(p), truth


def mark(df: pd.DataFrame, id_col: str, cited: set) -> pd.DataFrame:
    df = df.copy()
    df.insert(0, "flagged", df[id_col].isin(cited).map({True: "●", False: ""}))
    return df


# ------------------------------------------------------------------------------------------------ network graph
def network_dot(tb: Toolbox, links: dict, include_weak: bool = False) -> str:
    """Graphviz DOT: the customer, the identifiers they share, and the accounts on the other end."""
    colour = {"strong": "#d1495b", "medium": "#edae49", "weak": "#9aa5b1"}
    L = ['graph G {', 'bgcolor="transparent"; rankdir=LR; splines=true; overlap=false;',
         'node [style="filled,rounded", shape=box, fontname="Helvetica", fontsize=10, fontcolor="#111111"];',
         'edge [fontname="Helvetica", fontsize=9, fontcolor="#777777"];',
         f'"{tb.customer_id}" [label="{tb.customer_id}\\n(this account)", fillcolor="#8fb8de", shape=ellipse];']
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


# ------------------------------------------------------------------------------------------------ slide 2: profile
def render_profile(tb: Toolbox) -> None:
    p = tb.get_customer_profile()
    idp = tb.get_identity_profile() if tb.data.has_identity else {}
    st.markdown(f"## {p['customer_name']}  \n`{p['customer_id']}` · {tb.alert['alert_id']} · {tb.alert['rule_name']}")
    a, b, c, d, e = st.columns(5)
    a.metric("Type", p.get("customer_category") or p.get("customer_type", ""))
    b.metric("Date joined", str(p.get("onboarding_date") or idp.get("signup_date") or "-"))
    c.metric("Risk rating", str(p.get("risk_rating", "-")))
    d.metric("Account status", str(idp.get("account_status", "ACTIVE")))
    e.metric("Expected inflow / month", f"EUR {float(p.get('expected_monthly_inflow_eur') or 0):,.0f}")
    business = p.get("business_type") or ""
    rows = [("Email", idp.get("email")), ("Date of birth", p.get("date_of_birth")), ("Address", p.get("address")),
            ("Phone", idp.get("phone")), ("Occupation / activity", p.get("occupation")),
            ("Type of business", business if business and str(business) != "nan" else None)]
    st.markdown(" · ".join(f"**{k}** {v}" for k, v in rows if v and str(v) != "nan"))
    st.caption(tb.alert["rule_description"])


# ------------------------------------------------------------------------------------------------ slide 7: recommendation
def render_recommendation(res: dict) -> None:
    d, g = res["decision"], res["guardrails"]
    icon, text = STATUS_STYLE[g.final_status]
    st.markdown("### Agent recommendation")
    c1, c2 = st.columns([3, 2])
    with c1:
        st.subheader(f"{icon} {LABELS[d.disposition]}")
        m1, m2, m3 = st.columns(3)
        m1.metric("Confidence", f"{d.confidence:.0%}")
        m2.metric("Risk score", f"{d.score:.2f}", help="Probability the activity is suspicious; used for ranking and ROC AUC")
        m3.metric("Evidence items", len(d.flagged_items) or len(d.evidence_txn_ids))
        st.markdown(f"**Typology:** {', '.join(t.replace('_', ' ') for t in d.typologies)}  \n**Routing:** {text}")
        for o in g.overrides:
            st.warning(o)
    with c2:
        st.markdown(f"#### {ACTION_ICON[d.account_action]} {ACTION_LABELS[d.account_action]}")
        st.write(d.account_action_reason or "-")
        if d.documents_requested:
            st.markdown("**Documents to request** (neutral wording, no mention of suspicion)")
            for doc in d.documents_requested:
                st.markdown(f"- {doc}")
        st.markdown("**SAR: why and when**")
        st.write(d.sar_reasoning or "-")

    if d.flagged_items:
        st.markdown("**Why each item was flagged**")
        fl = pd.DataFrame(d.flagged_items)
        grouped = (fl.groupby("reason", sort=False)
                     .agg(items=("id", lambda x: ", ".join(x)), kinds=("kind", lambda x: ", ".join(sorted(set(x)))), n=("id", "size"))
                     .reset_index()[["reason", "kinds", "n", "items"]])
        st.dataframe(grouped, hide_index=True, width="stretch",
                     column_config={"reason": st.column_config.TextColumn("Why", width="large"),
                                    "kinds": "What", "n": "Count", "items": st.column_config.TextColumn("IDs", width="medium")})
    elif d.risk_indicators:
        for i in d.risk_indicators:
            st.markdown(f"- {i}")
    with st.expander("Draft narrative, rationale and guardrail checks", expanded=False):
        st.text_area("narrative", d.narrative, height=150, label_visibility="collapsed")
        st.caption(f"Agent rationale: {d.rationale}")
        for c in g.checks:
            st.markdown(f"{'✅' if c['passed'] else '❌'} `{c['name']}` {c['detail']}")
        st.caption(f"Audit trail: {res['audit_path']}")


# ------------------------------------------------------------------------------------------------ slide 8: analyst decision
def render_decision_form(aid: str, res: dict | None) -> None:
    st.markdown("### Analyst decision")
    d = res["decision"] if res else None
    with st.form(f"decision_{aid}"):
        c1, c2 = st.columns(2)
        options = list(ACTION_LABELS)
        default = options.index(d.account_action) if d else options.index("NO_ACTION")
        acct = c1.radio("Account", options, index=default, format_func=lambda x: f"{ACTION_ICON[x]} {ACTION_LABELS[x]}")
        sar_default = 0 if d and d.disposition == "RECOMMEND_SAR" else 1
        sar = c2.radio("SAR", ["FILE_SAR", "NO_SAR"], index=sar_default,
                       format_func=lambda x: "File SAR" if x == "FILE_SAR" else "No SAR")
        reason = st.text_input("Reason (required when you disagree with the agent)")
        reviewer = st.text_input("Analyst", value="analyst")
        if st.form_submit_button("Record decision", type="primary"):
            disagrees = d is not None and (acct != d.account_action or (sar == "FILE_SAR") != (d.disposition == "RECOMMEND_SAR"))
            if disagrees and not reason.strip():
                st.error("Please give a reason: your correction is what the agent learns from.")
            else:
                learning.record_review(aid, acct, sar, reason or "Agree with the agent.", reviewer, d)
                st.success("Decision recorded. It will be used the next time the model is trained (Learning page).")


# ------------------------------------------------------------------------------------------------ slides 3-6: evidence
def render_evidence(tb: Toolbox, cited: set) -> None:
    st.markdown("### Evidence")
    tabs = st.tabs(["Transactions", "Logins & activity", "Linked accounts", "Financials", "Disputes"])
    with tabs[0]:
        tx = pd.DataFrame(tb.get_transactions(period="all", limit=500)["transactions"])
        tx["to_from"] = [("To: " if dr == "OUT" else "From: ") + str(n) for dr, n in zip(tx.direction, tx.counterparty_name)]
        cols = ["txn_id", "timestamp", "amount_eur", "direction", "to_from", "counterparty_country", "channel", "reference"]
        cols += [c for c in ("disputed", "dispute_reason") if c in tx]
        tx = mark(tx[cols], "txn_id", cited)
        only = st.toggle("Only flagged transactions", value=bool(cited & set(tx.txn_id)), key=f"only_{tb.alert['alert_id']}")
        st.dataframe(tx[tx.flagged == "●"] if only else tx, hide_index=True, width="stretch")
    if not tb.data.has_identity:
        for t in tabs[1:]:
            t.info("No login data for this case. Upload logins.csv (and optionally instruments.csv, disputes.csv, "
                   "account_events.csv) to enable these views.")
        return
    ind = tb.compute_risk_indicators()
    with tabs[1]:
        acc = ind["access"]
        flags = []
        if acc["new_devices_window"]:
            flags.append(f"New device in the alert window: {', '.join(acc['new_devices_window'])} ({acc['new_device_login_share_window']:.0%} of logins)")
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
        idp = tb.get_identity_profile()
        st.markdown(f"**Sign-up** {idp.get('signup_date')} from IP `{idp.get('signup_ip')}` on device `{idp.get('signup_device_id')}` "
                    f"({idp.get('signup_country')})")
        lh = tb.get_login_history(limit=10)
        st.markdown(f"**Last 10 logins** · {lh['total_logins_90d']} logins, {lh['distinct_devices_90d']} devices, "
                    f"{lh['distinct_ips_90d']} IPs in 90 days · countries: {', '.join(map(str, lh['countries_90d']))}")
        logins = mark(pd.DataFrame(lh["logins"]), "login_id", cited)
        cols = [c for c in ["flagged", "login_id", "timestamp", "ip", "ip_type", "city", "country", "device_id", "visitor_id",
                            "new_device_in_window", "user_agent", "success"] if c in logins]
        st.dataframe(logins[cols], hide_index=True, width="stretch")
        geo = pd.DataFrame(tb.get_login_history(limit=100)["logins"])
        if {"latitude", "longitude"} <= set(geo) and geo[["latitude", "longitude"]].notna().all(axis=None) and len(geo):
            st.map(geo[["latitude", "longitude"]], zoom=1, height=280)
        st.markdown("**Account activity** (financials added or removed, contact details changed, password resets)")
        ev = pd.DataFrame(tb.get_account_activity()["events"])
        if len(ev):
            st.dataframe(mark(ev, "event_id", cited), hide_index=True, width="stretch")
        else:
            st.caption("No account events.")
    with tabs[2]:
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
            st.caption("Red accounts are closed, limited or previously reported. Link colour: red strong (device, bank account, "
                       "phone, email), amber medium (browser ID, small shared household IP), grey weak (IP shared more widely).")
            rows = [{"flagged": "●" if l["customer_id"] in cited else "", "customer_id": l["customer_id"],
                     "name": l["customer_name"], "status": l["account_status"], "reason": l["status_reason"],
                     "prior_sar": l["prior_sar"], "strongest_link": l["strongest_link"],
                     "shared": "; ".join(f"{s['identifier_type']} {s['value']} ({s['strength']}, {s['accounts_sharing']} accts)"
                                         for s in l["shared"])}
                    for l in links["linked_accounts"]]
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        else:
            st.info("No linked accounts beyond suppressed high-cardinality identifiers.")
        with st.expander(f"Suppressed identifiers ({len(links['suppressed_high_cardinality_identifiers'])})"):
            st.caption(links["note"])
            st.dataframe(pd.DataFrame(links["suppressed_high_cardinality_identifiers"]), hide_index=True, width="stretch")
    with tabs[3]:
        fin = tb.get_financials()

        def fin_table(rows):
            return pd.DataFrame([{**{k: v for k, v in r.items() if k != "also_on_accounts"},
                                  "also_on_other_accounts": ", ".join(f"{o['customer_id']} ({o['account_status']}"
                                                                      f"{', SAR' if o['prior_sar'] else ''})" for o in r["also_on_accounts"]) or "-"}
                                 for r in rows])
        st.markdown(f"**Active financials** ({len(fin['active'])})")
        if fin["active"]:
            st.dataframe(fin_table(fin["active"]), hide_index=True, width="stretch")
        st.markdown(f"**Past financials** ({len(fin['removed'])})")
        if fin["removed"]:
            st.dataframe(fin_table(fin["removed"]), hide_index=True, width="stretch")
        else:
            st.caption("None removed.")
        shared = [r for r in fin["active"] + fin["removed"] if r["also_on_accounts"]]
        if shared:
            st.error(f"{len(shared)} bank account(s) or card(s) on this account also appear on other accounts.")
        act = ind.get("activity", {})
        if act.get("financial_added_then_removed_window"):
            st.warning("A financial was added in the alert window and removed again after use.")
    with tabs[4]:
        ds = tb.get_disputes()
        dd = ind["disputes"]
        a, b, c = st.columns(3)
        a.metric("Disputes", ds["count"])
        b.metric("Payer claims: unauthorised / scam", dd["victim_claims"], help=f"{dd.get('victim_claims_not_withdrawn', '-')} not withdrawn")
        c.metric("Buyer claims", dd["buyer_claims"])
        if ds["count"]:
            st.dataframe(mark(pd.DataFrame(ds["disputes"]), "dispute_id", cited), hide_index=True, width="stretch")
        else:
            st.success("No disputes on this account.")


# ------------------------------------------------------------------------------------------------ sidebar (slide 1)
st.sidebar.title("AML Transaction Monitoring")
source = st.sidebar.radio("Data", ["Demo data", "Upload your own"], horizontal=True)
data, truth, demo = None, None, source == "Demo data"
if demo:
    data, truth = load_demo()
else:
    files = st.sidebar.file_uploader("Upload file here: CSVs or a .zip", type=["csv", "zip"], accept_multiple_files=True)
    st.sidebar.download_button("Download a template (.zip)", upload.template_zip(), file_name="aml_upload_template.zip",
                               help="Example of every file and its columns. Only transactions.csv is required.")
    if files:
        tables, errors, notes = upload.prepare(upload.read_uploads({f.name: f.getvalue() for f in files}))
        for e in errors:
            st.sidebar.error(e)
        for n in notes:
            st.sidebar.info(n)
        if not errors:
            data, truth = load_folder(str(upload.save(tables)))
            st.sidebar.success(f"Loaded {len(data.alerts)} alert(s), {len(data.transactions)} transactions.")

page = st.sidebar.radio("View", ["Case review", "Evaluation", "Learning"])
available = ["baseline", "baseline_v2", "baseline_v1"] + (["learned"] if learning.load_model() else []) + \
            (["claude"] if os.getenv("ANTHROPIC_API_KEY") else [])
agent_kind = st.sidebar.selectbox("Agent", available, format_func=AGENTS.get,
                                  help="Train the learned agent on the Learning page. The Claude agent needs ANTHROPIC_API_KEY.")
show_truth = truth is not None and st.sidebar.toggle("Show ground truth (evaluation only)", value=False)
st.sidebar.caption("Demo data is synthetic: fictional people, reserved IPs and phone ranges. The agent recommends; people decide.")

if data is None and page == "Case review":
    st.title("AML Transaction Monitoring")
    st.info("Upload your files in the sidebar (only transactions.csv is required), or switch to the demo data. "
            "Download the template to see the expected columns.")
    st.stop()

# ------------------------------------------------------------------------------------------------ case review
if page == "Case review":
    alerts = data.alerts.merge(truth, on="alert_id", how="left") if show_truth else data.alerts
    label = lambda r: f"{r.alert_id} · {r.rule_name} · {r.customer_id}" + (f" · [{r.scenario}]" if show_truth else "")
    options = {label(r): r.alert_id for r in alerts.itertuples()}
    ids = list(options.values())
    wanted = st.query_params.get("alert")                     # deep link: ?alert=ALR-0006
    aid = options[st.selectbox("Alert queue", list(options), index=ids.index(wanted) if wanted in ids else 0)]
    tb = Toolbox(data, aid)

    render_profile(tb)
    if show_truth:
        t = truth.set_index("alert_id").loc[aid]
        st.info(f"Ground truth: {'SUSPICIOUS' if t.is_suspicious else 'benign'} · {t.scenario} ({t.variant}) · "
                f"expected action: {ACTION_LABELS.get(t.get('expected_action', ''), '-')}")

    key = f"result_{aid}_{agent_kind}_{source}"
    if st.button("Run investigation", type="primary"):
        with st.spinner("Agent investigating…"):
            st.session_state[key] = investigate(aid, make_agent(agent_kind), data, run_id="app", write_audit=True)
    res = st.session_state.get(key)
    if res:
        render_recommendation(res)
    else:
        st.caption("Run the investigation for the agent's recommendation. The evidence below is available now.")
    render_decision_form(aid, res)
    cited = set(res["decision"].evidence_txn_ids) | {f["id"] for f in res["decision"].flagged_items} if res else set()
    render_evidence(tb, cited)

# ------------------------------------------------------------------------------------------------ evaluation
elif page == "Evaluation":
    st.title("Evaluation")
    st.caption("Scored against labelled ground truth (demo data). Accuracy, precision, recall, F1, ROC AUC, and whether the "
               "recommended account action (ban / restrict / request documents / no action) was the right one.")
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
        st.subheader(f"{AGENTS.get(tag, tag)} · {m['alerts']} alerts")
        cols = st.columns(6)
        cols[0].metric("Accuracy", f"{m.get('accuracy', 0):.1%}")
        cols[1].metric("Recall", f"{m['model_recall']:.1%}", help="Suspicious alerts escalated by the agent")
        cols[2].metric("Precision", f"{m.get('precision', 0):.1%}")
        cols[3].metric("ROC AUC", f"{m.get('roc_auc') or 0:.3f}")
        cols[4].metric("Action accuracy", f"{m.get('action_accuracy', 0):.1%}", help="Ban / restrict / documents / no action")
        cols[5].metric("Restrict/close on benign", f"{m.get('harmful_action_rate_benign', 0):.1%}")
        a, b = st.columns(2)
        a.markdown("**Recall by scenario**")
        a.bar_chart(pd.Series({k: v["rate"] for k, v in m["recall_by_scenario"].items()}), horizontal=True)
        b.markdown("**False-positive clearance by scenario**")
        b.bar_chart(pd.Series({k: v["rate"] for k, v in m["clearance_by_scenario"].items()}), horizontal=True)
        rep = C.REPORTS_DIR / f"eval_report_{tag}.md"
        if rep.exists():
            with st.expander("Full report"):
                st.markdown(rep.read_text(encoding="utf-8"))

# ------------------------------------------------------------------------------------------------ learning
else:
    st.title("Learning from analyst decisions")
    st.caption("Every decision recorded on the Case review page is a labelled example. The learned agent is retrained on "
               "them and scored only on alerts no analyst has decided. Disagreements, with their reasons, are also given "
               "to the Claude agent as lessons.")
    rv = learning.load_reviews()
    labels = [learning.review_label(r) for r in rv.to_dict("records")] if len(rv) else []
    a, b, c = st.columns(3)
    a.metric("Analyst decisions", len(rv))
    b.metric("Treated as suspicious", int(sum(1 for x in labels if x == 1)))
    lessons = learning.feedback_examples(limit=50)
    c.metric("Disagreements with the agent", len(lessons))
    c1, c2 = st.columns(2)
    if c1.button("Simulate an analyst deciding half the demo queue", help="Demo only: uses ground truth as the analyst."):
        with st.spinner("Simulating analyst decisions…"):
            n = learning.simulate(0.5)
        st.success(f"Recorded {n} simulated decisions.")
        st.rerun()
    if c2.button("Train the model on analyst decisions", type="primary"):
        try:
            with st.spinner("Training…"):
                m = learning.train()
            st.success(f"Trained on {m['n_train']} decisions. 5-fold cross-validated ROC AUC {m['cv_roc_auc']}. "
                       "Run `python -m amlagent.evaluate --agent learned` to score it on held-out alerts.")
            st.cache_resource.clear()
        except SystemExit as e:
            st.error(str(e))
    model = learning.load_model()
    if model:
        st.subheader("What the model learned")
        st.caption(f"Trained {model['trained_at'][:16]} on {model['n_train']} decisions ({model['n_suspicious']} suspicious). "
                   f"Cross-validated ROC AUC {model['cv_roc_auc']}. Positive weight = analysts treat it as a sign of suspicion.")
        import altair as alt
        w = learning.top_weights(model, 15)
        w["signal"] = w.feature.str.replace("_", " ").str.replace(".", ": ", regex=False)
        w["direction"] = w.weight.map(lambda x: "more suspicious" if x > 0 else "less suspicious")
        st.altair_chart(alt.Chart(w).mark_bar().encode(
            x=alt.X("weight:Q", title="Weight (standardised)"),
            y=alt.Y("signal:N", sort=alt.EncodingSortField("weight", op="sum", order="descending"), title=None,
                    axis=alt.Axis(labelLimit=380)),
            color=alt.Color("direction:N", scale=alt.Scale(domain=["more suspicious", "less suspicious"],
                                                           range=["#d1495b", "#4c956c"]), title=None),
            tooltip=["signal", "weight"]).properties(height=420), width="stretch")
        hp, lp = C.REPORTS_DIR / "metrics_baseline_holdout.json", C.REPORTS_DIR / "metrics_learned.json"
        if hp.exists() and lp.exists():
            h, l = json.loads(hp.read_text(encoding="utf-8")), json.loads(lp.read_text(encoding="utf-8"))
            st.subheader(f"On {l['alerts']} alerts no analyst has decided")
            keys = [("accuracy", "Accuracy"), ("model_recall", "Recall"), ("precision", "Precision"), ("roc_auc", "ROC AUC"),
                    ("fp_clearance_rate", "False positives cleared"), ("action_accuracy", "Action accuracy")]
            st.dataframe(pd.DataFrame({"Rules v3": [h.get(k) for k, _ in keys], "Learned": [l.get(k) for k, _ in keys]},
                                      index=[n for _, n in keys]), width="stretch")
    if lessons:
        st.subheader("Lessons passed to the Claude agent")
        st.dataframe(pd.DataFrame(lessons[-10:]), hide_index=True, width="stretch")
