"""Tests for tools, guardrails and the LLM agent loop (using a scripted fake client, no API key needed)."""
from __future__ import annotations

from types import SimpleNamespace as NS

import pandas as pd
import pytest

from amlagent import config as C
from amlagent.agents.baseline import BaselineAgent
from amlagent.agents.claude_agent import ClaudeAgent
from amlagent.audit import AuditLog
from amlagent.data_gen import generate
from amlagent.guardrails import apply_guardrails
from amlagent.pipeline import investigate
from amlagent.schema import AUTO_CLOSE_ELIGIBLE, HUMAN_REVIEW, SAR_PENDING, Decision
from amlagent.tools import CaseData, Toolbox


@pytest.fixture(scope="module")
def data(tmp_path_factory):
    d = tmp_path_factory.mktemp("data")
    for name, df in generate(seed=7).items():
        df.to_csv(d / f"{name}.csv", index=False)
    return CaseData.load(d), pd.read_csv(d / "ground_truth.csv")


def first_alert(truth, scenario):
    return truth.loc[truth.scenario == scenario, "alert_id"].iloc[0]


def test_generation_is_deterministic():
    a, b = generate(seed=3), generate(seed=3)
    pd.testing.assert_frame_equal(a["transactions"], b["transactions"])


def test_toolbox_is_scoped_to_one_customer(data):
    cd, truth = data
    tb = Toolbox(cd, truth.alert_id.iloc[0])
    tx = tb.get_transactions(period="all", limit=300)["transactions"]
    assert tx and all(t["txn_id"] in tb.all_txn_ids() for t in tx)


def test_fabricated_citation_is_caught_and_routed_to_human(data):
    cd, truth = data
    aid = first_alert(truth, "one_off_large_credit")
    tb = Toolbox(cd, aid)
    d = Decision(aid, "CLOSE_FALSE_POSITIVE", ["none"], 0.99, [], ["TXN-999999"], "r",
                 f"{tb.customer_id} closed, see TXN-999999")
    g = apply_guardrails(d, tb)
    assert "citations_grounded" in [c["name"] for c in g.violations]
    assert g.final_status == HUMAN_REVIEW


def test_untraceable_amount_is_caught(data):
    cd, truth = data
    aid = first_alert(truth, "structuring")
    tb = Toolbox(cd, aid)
    real = sorted(tb.all_txn_ids())[:2]
    d = Decision(aid, "ESCALATE_L2", ["structuring"], 0.8, [], real, "r",
                 f"{tb.customer_id} deposited EUR 123,456.78 in cash ({', '.join(real)})")
    assert "amounts_traceable" in [c["name"] for c in apply_guardrails(d, tb).violations]


def test_low_confidence_close_is_not_auto_closed(data):
    cd, truth = data
    aid = first_alert(truth, "online_retailer")
    tb = Toolbox(cd, aid)
    d = Decision(aid, "CLOSE_FALSE_POSITIVE", ["none"], C.AUTO_CLOSE_MIN_CONFIDENCE - 0.1, [], [], "r", tb.customer_id)
    assert apply_guardrails(d, tb).final_status == HUMAN_REVIEW
    d.confidence = 0.95
    assert apply_guardrails(d, tb).final_status == AUTO_CLOSE_ELIGIBLE


def test_strong_sanctions_match_cannot_be_closed(data):
    cd, truth = data
    for aid in truth.loc[truth.scenario == "sanctions_exposure", "alert_id"]:
        tb = Toolbox(cd, aid)
        if any(h["is_potential_match"] and h["country_matches"] for h in tb.screen_counterparties()["results"]):
            d = Decision(aid, "CLOSE_FALSE_POSITIVE", ["none"], 0.99, [], [], "r", tb.customer_id)
            g = apply_guardrails(d, tb)
            assert g.final_disposition == "ESCALATE_L2" and g.overrides
            return
    pytest.skip("no strong match in this seed")


def test_sar_is_never_filed_automatically(data):
    cd, truth = data
    r = investigate(first_alert(truth, "structuring"), BaselineAgent(), cd, "test", write_audit=False)
    assert r["decision"].disposition == "RECOMMEND_SAR"
    assert r["guardrails"].final_status == SAR_PENDING


# --- LLM agent loop with a scripted fake client ---------------------------------------------------

def _block_tool(name, inp, i):
    return NS(type="tool_use", name=name, input=inp, id=f"tu_{i}")


class FakeClient:
    """Replays a fixed sequence of assistant turns; records what the agent sent back."""

    def __init__(self, turns):
        self.turns, self.sent = list(turns), []
        self.messages = self

    def create(self, **kw):
        self.sent.append(kw["messages"][-1])
        content = self.turns.pop(0)
        return NS(content=content, usage=NS(input_tokens=100, output_tokens=50))


def test_claude_agent_loop_calls_tools_and_submits(data):
    cd, truth = data
    aid = first_alert(truth, "structuring")
    tb = Toolbox(cd, aid)
    ev = tb.compute_risk_indicators()["cash"]["cash_txn_ids"]
    client = FakeClient([
        [NS(type="text", text="Start with the profile and indicators."),
         _block_tool("get_customer_profile", {}, 1), _block_tool("compute_risk_indicators", {}, 2)],
        [_block_tool("submit_decision", {
            "disposition": "RECOMMEND_SAR", "typologies": ["structuring"], "confidence": 0.9,
            "risk_indicators": ["cash near threshold"], "evidence_txn_ids": ev, "rationale": "r",
            "narrative": f"{tb.customer_id}: structuring via {', '.join(ev)}"}, 3)],
    ])
    agent = ClaudeAgent(model="test-model", client=client)
    audit = AuditLog("t", aid, enabled=False)
    decision, ctx = agent.investigate(tb, audit)
    assert decision.disposition == "RECOMMEND_SAR" and decision.evidence_txn_ids == ev
    tool_results = client.sent[1]["content"]
    assert [r["tool_use_id"] for r in tool_results] == ["tu_1", "tu_2"]
    assert [e["tool"] for e in audit.events if e["event"] == "tool_call"] == ["get_customer_profile", "compute_risk_indicators"]
    assert ctx["usage"]["input_tokens"] == 200


def test_claude_agent_fails_safe_on_turn_limit(data):
    cd, truth = data
    aid = first_alert(truth, "online_retailer")
    client = FakeClient([[_block_tool("get_alert", {}, i)] for i in range(3)])
    decision, _ = ClaudeAgent(model="m", client=client, max_turns=3).investigate(Toolbox(cd, aid), AuditLog("t", aid, enabled=False))
    assert decision.disposition == "ESCALATE_L2" and decision.confidence == 0.0
