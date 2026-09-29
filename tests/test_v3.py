"""Tests for v3: account actions, tipping-off guardrail, metrics, uploads and the learning loop."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from amlagent import config as C
from amlagent import learning, upload
from amlagent.agents.baseline import BaselineAgent
from amlagent.audit import AuditLog
from amlagent.data_gen import generate
from amlagent.evaluate import roc_auc
from amlagent.guardrails import apply_guardrails
from amlagent.pipeline import investigate
from amlagent.schema import Decision
from amlagent.tools import CaseData, Toolbox


@pytest.fixture(scope="module")
def frames():
    return generate(seed=13)


@pytest.fixture(scope="module")
def data(frames, tmp_path_factory):
    d = tmp_path_factory.mktemp("data")
    for name, df in frames.items():
        df.to_csv(d / f"{name}.csv", index=False)
    return CaseData.load(d), pd.read_csv(d / "ground_truth.csv")


def first(truth, scenario, variant="standard"):
    return truth[(truth.scenario == scenario) & (truth.variant == variant)].alert_id.iloc[0]


def test_earlier_layers_unchanged_by_v3(frames):
    from amlagent.identity_gen import add_identity_layer
    base = generate(seed=13, with_identity=False)
    v2 = add_identity_layer({k: v.copy() for k, v in base.items()}, 13)
    pd.testing.assert_frame_equal(v2["logins"], frames["logins"])
    pd.testing.assert_frame_equal(base["transactions"], frames["transactions"])


def test_profile_has_dob_address_and_category(data):
    cd, truth = data
    p = Toolbox(cd, truth.alert_id.iloc[0]).get_customer_profile()
    assert {"date_of_birth", "address", "customer_category", "business_type"} <= set(p)


def test_mule_financial_added_then_removed_is_visible(data):
    cd, truth = data
    tb = Toolbox(cd, first(truth, "money_mule"))
    fin = tb.get_financials()
    assert any(r["also_on_accounts"] for r in fin["removed"])
    assert tb.compute_risk_indicators()["activity"]["financial_added_then_removed_window"]


def test_transactions_carry_dispute_flag(data):
    cd, truth = data
    tb = Toolbox(cd, first(truth, "money_mule"))
    rows = tb.get_transactions(period="all", limit=500)["transactions"]
    assert {"Y", "N"} >= {r["disputed"] for r in rows} and any(r["disputed"] == "Y" for r in rows)


def test_strong_mule_gets_close_account_and_sar_reasoning(data):
    cd, truth = data
    r = investigate(first(truth, "money_mule"), BaselineAgent(level=3), cd, "t", write_audit=False)
    d = r["decision"]
    assert d.account_action == "CLOSE_ACCOUNT" and "SAR" in d.sar_reasoning and d.flagged_items
    assert any("MLRO" in o for o in r["guardrails"].overrides)


def test_tipping_off_wording_is_caught(data):
    cd, truth = data
    aid = first(truth, "structuring")
    tb = Toolbox(cd, aid)
    ev = tb.compute_risk_indicators()["cash"]["cash_txn_ids"]
    d = Decision(aid, "RECOMMEND_SAR", ["structuring"], 0.9, [], ev, "r", f"{tb.customer_id}",
                 account_action="REQUEST_DOCUMENTS",
                 documents_requested=["Please explain these suspicious deposits, which we may report"])
    assert "tipping_off_wording" in [c["name"] for c in apply_guardrails(d, tb).violations]
    d.documents_requested = ["Source of funds for the cash deposited", "Is this necessary? Payslips welcome"]
    assert "tipping_off_wording" not in [c["name"] for c in apply_guardrails(d, tb).violations]


def test_ban_on_a_closed_alert_is_inconsistent(data):
    cd, truth = data
    aid = first(truth, "online_retailer")
    tb = Toolbox(cd, aid)
    d = Decision(aid, "CLOSE_FALSE_POSITIVE", ["none"], 0.95, [], [], "r", tb.customer_id, account_action="CLOSE_ACCOUNT")
    assert "action_consistent" in [c["name"] for c in apply_guardrails(d, tb).violations]


def test_roc_auc():
    y = np.array([1, 1, 0, 0])
    assert roc_auc(y, np.array([0.9, 0.8, 0.2, 0.1])) == 1.0
    assert roc_auc(y, np.array([0.1, 0.2, 0.8, 0.9])) == 0.0
    assert roc_auc(y, np.array([0.5, 0.5, 0.5, 0.5])) == 0.5


def test_upload_transactions_only(frames):
    tx = frames["transactions"]
    tx = tx[tx.customer_id.isin(tx.customer_id.unique()[:3])]
    tables, errors, notes = upload.prepare({"transactions": tx[["txn_id", "customer_id", "timestamp", "direction",
                                                                 "amount_eur", "counterparty_name"]]})
    assert not errors and len(tables["alerts"]) == 3 and notes


def test_upload_rejects_bad_files(frames):
    tx = frames["transactions"].head(5).drop(columns=["amount_eur"])
    assert upload.prepare({"transactions": tx})[1]
    tx = frames["transactions"].head(5).assign(direction="SIDEWAYS")
    assert upload.prepare({"transactions": tx})[1]


def test_learning_features_and_scoring_roundtrip(data):
    cd, truth = data
    X = learning.feature_matrix(cd, truth.alert_id.tolist()[:40])
    assert len(X) == 40 and X.shape[1] > 20 and not X.isna().any().any()
    from sklearn.linear_model import LogisticRegression
    y = truth.set_index("alert_id").loc[X.index, "is_suspicious"].to_numpy()
    X = X.loc[:, X.std() > 0]
    mu, sd = X.mean(), X.std()
    clf = LogisticRegression(max_iter=2000).fit((X - mu) / sd, y)
    model = {"features": X.columns.tolist(), "mean": mu.tolist(), "std": sd.tolist(), "coef": clf.coef_[0].tolist(),
             "intercept": float(clf.intercept_[0])}
    aid = X.index[0]
    p = learning.score(model, Toolbox(cd, aid).compute_risk_indicators())
    assert abs(p - clf.predict_proba(((X.loc[[aid]] - mu) / sd))[0, 1]) < 1e-6


def test_review_labels():
    assert learning.review_label({"account_decision": "CLOSE_ACCOUNT", "sar_decision": "FILE_SAR"}) == 1
    assert learning.review_label({"account_decision": "NO_ACTION", "sar_decision": "NO_SAR"}) == 0
    assert learning.review_label({"decision": "Override: close"}) == 0
