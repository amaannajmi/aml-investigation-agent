"""Tests for the v2 identity, access, network and dispute layer."""
from __future__ import annotations

import pandas as pd
import pytest

from amlagent import config as C
from amlagent.agents.baseline import BaselineAgent
from amlagent.audit import AuditLog
from amlagent.data_gen import generate
from amlagent.guardrails import apply_guardrails
from amlagent.schema import Decision
from amlagent.tools import CaseData, Toolbox


@pytest.fixture(scope="module")
def data(tmp_path_factory):
    d = tmp_path_factory.mktemp("data")
    for name, df in generate(seed=11).items():
        df.to_csv(d / f"{name}.csv", index=False)
    return CaseData.load(d), pd.read_csv(d / "ground_truth.csv")


def alerts_for(truth, scenario, variant=None):
    t = truth[truth.scenario == scenario]
    if variant:
        t = t[t.variant == variant]
    return t.alert_id.tolist()


def test_identity_layer_does_not_change_transactions():
    a = generate(seed=5, with_identity=False)["transactions"]
    b = generate(seed=5)["transactions"]
    pd.testing.assert_frame_equal(a, b)


def test_standard_mule_is_linked_to_closed_account_by_strong_identifier(data):
    cd, truth = data
    tb = Toolbox(cd, alerts_for(truth, "money_mule", "standard")[0])
    net = tb.compute_risk_indicators()["network"]
    assert net["adverse_links_medium_or_strong"] >= 1
    assert {"device_id", "bank_fingerprint"} & set(net["adverse_shared_identifier_types"])


def test_high_cardinality_identifiers_are_suppressed(data):
    cd, truth = data
    tb = Toolbox(cd, truth.alert_id.iloc[0])
    links = tb.get_linked_accounts()
    assert all(s["accounts_sharing"] > C.LINK_SUPPRESS_CARDINALITY for s in links["suppressed_high_cardinality_identifiers"])
    assert all(sh["accounts_sharing"] <= C.LINK_SUPPRESS_CARDINALITY
               for l in links["linked_accounts"] for sh in l["shared"])


def test_shared_household_ip_is_a_medium_link_to_clean_accounts(data):
    cd, truth = data
    tb = Toolbox(cd, alerts_for(truth, "shared_expenses")[0])
    links = tb.get_linked_accounts()["linked_accounts"]
    household = [l for l in links if any(s["identifier_type"] == "ip" and s["strength"] == "medium" for s in l["shared"])]
    assert household and not any(l["is_adverse"] for l in household)


def test_login_history_is_newest_first_and_limited(data):
    cd, truth = data
    lh = Toolbox(cd, truth.alert_id.iloc[3]).get_login_history(limit=10)
    ts = [r["timestamp"] for r in lh["logins"]]
    assert len(ts) <= 10 and ts == sorted(ts, reverse=True)


def test_grounding_accepts_own_login_and_dispute_ids_but_not_other_customers(data):
    cd, truth = data
    aid = alerts_for(truth, "money_mule", "standard")[0]
    tb = Toolbox(cd, aid)
    own_login = tb.get_login_history(limit=1)["logins"][0]["login_id"]
    own_dispute = tb.get_disputes()["disputes"][0]["dispute_id"]
    other = cd.logins[cd.logins.customer_id != tb.customer_id].login_id.iloc[0]
    ok = Decision(aid, "ESCALATE_L2", ["money_mule"], 0.8, [], [own_login, own_dispute], "r",
                  f"{tb.customer_id}: see {own_login} and {own_dispute}")
    assert "citations_grounded" not in [c["name"] for c in apply_guardrails(ok, tb).violations]
    bad = Decision(aid, "ESCALATE_L2", ["money_mule"], 0.8, [], [other], "r", f"{tb.customer_id}: see {other}")
    assert "citations_grounded" in [c["name"] for c in apply_guardrails(bad, tb).violations]


def test_v2_baseline_uses_network_evidence_that_v1_cannot_see(data):
    cd, truth = data
    aid = alerts_for(truth, "mule_cash_out", "standard")[0]
    v1, _ = BaselineAgent(use_identity=False).investigate(Toolbox(cd, aid), AuditLog("t", aid, enabled=False))
    v2, _ = BaselineAgent(use_identity=True).investigate(Toolbox(cd, aid), AuditLog("t", aid, enabled=False))
    assert v2.is_escalation
    assert any("closed/limited" in i or "payer claim" in i for i in v2.risk_indicators)
    assert not any("closed/limited" in i for i in v1.risk_indicators)
