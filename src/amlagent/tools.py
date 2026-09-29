"""Investigation tools: the only way an agent can see data.

Each tool is a plain function over the case data. Every call is logged by the
audit trail, so a reviewer can replay exactly what the agent looked at. The
same tools back both the deterministic baseline agent and the LLM agent,
which keeps the comparison fair.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import cached_property

import pandas as pd
from rapidfuzz import fuzz, process

from . import config as C


@dataclass
class CaseData:
    customers: pd.DataFrame
    transactions: pd.DataFrame
    alerts: pd.DataFrame
    sanctions: pd.DataFrame

    @classmethod
    def load(cls, data_dir=C.DATA_DIR) -> "CaseData":
        tx = pd.read_csv(data_dir / "transactions.csv")
        tx["timestamp"] = pd.to_datetime(tx["timestamp"], format="ISO8601")
        return cls(
            customers=pd.read_csv(data_dir / "customers.csv").fillna(""),
            transactions=tx.fillna({"reference": ""}),
            alerts=pd.read_csv(data_dir / "alerts.csv"),
            sanctions=pd.read_csv(data_dir / "sanctions_list.csv"),
        )

    @cached_property
    def _tx_by_customer(self) -> dict[str, pd.DataFrame]:
        return {cid: g.sort_values("timestamp") for cid, g in self.transactions.groupby("customer_id")}

    def txns(self, customer_id: str) -> pd.DataFrame:
        return self._tx_by_customer.get(customer_id, self.transactions.iloc[0:0])


class Toolbox:
    """Tools bound to one alert. Agents cannot query other customers."""

    def __init__(self, data: CaseData, alert_id: str):
        row = data.alerts.loc[data.alerts.alert_id == alert_id]
        if row.empty:
            raise KeyError(f"Unknown alert {alert_id}")
        self.data = data
        self.alert = row.iloc[0].to_dict()
        self.customer_id = self.alert["customer_id"]
        self.as_of = datetime.fromisoformat(self.alert["alert_date"]) + timedelta(hours=23, minutes=59)
        self.window_start = self.as_of - timedelta(days=int(self.alert["lookback_days"]))
        self.history_start = self.window_start - timedelta(days=C.HISTORY_DAYS)

    # ---------------------------------------------------------------- tools
    def get_alert(self) -> dict:
        """The alert as raised by the monitoring system."""
        return dict(self.alert)

    def get_customer_profile(self) -> dict:
        """KYC profile: type, occupation/business activity, expected activity, risk rating."""
        c = self.data.customers.loc[self.data.customers.customer_id == self.customer_id].iloc[0].to_dict()
        return {k: (float(v) if k == "expected_monthly_inflow_eur" else v) for k, v in c.items()}

    def get_transactions(self, period: str = "window", direction: str = "ALL", min_amount: float = 0.0,
                         limit: int = 150) -> dict:
        """Transactions for the alerted customer. period: 'window' (alert lookback) | 'history' (the
        60 days before the window) | 'all'. Card spend under EUR 150 is summarised, not listed."""
        tx = self._period(period)
        if direction in ("IN", "OUT"):
            tx = tx[tx.direction == direction]
        tx = tx[tx.amount_eur >= min_amount]
        small_card = tx[(tx.channel == "CARD") & (tx.amount_eur < 150)]
        listed = tx.drop(small_card.index)
        rows = listed.head(limit).assign(timestamp=lambda d: d.timestamp.dt.strftime("%Y-%m-%d %H:%M"))
        return {
            "customer_id": self.customer_id, "period": period,
            "count": int(len(listed)), "truncated": bool(len(listed) > limit),
            "small_card_spend_summary": {"count": int(len(small_card)), "total_eur": round(float(small_card.amount_eur.sum()), 2)},
            "transactions": rows.drop(columns=["customer_id"]).to_dict("records"),
        }

    def screen_counterparties(self) -> dict:
        """Fuzzy-screen the customer and every counterparty in the full period against the sanctions list."""
        tx = self._period("all")
        names = {self.get_customer_profile()["customer_name"]: None}
        for n, c in tx[["counterparty_name", "counterparty_country"]].drop_duplicates().itertuples(index=False):
            names.setdefault(n, c)
        choices = self.data.sanctions.name.tolist()
        hits = []
        for name, country in names.items():
            best = process.extractOne(name, choices, scorer=fuzz.token_sort_ratio)
            if best and best[1] >= 60:          # surface near-misses too, for context
                entry = self.data.sanctions.iloc[best[2]]
                related = tx[tx.counterparty_name == name]
                hits.append({
                    "screened_name": name, "screened_country": country,
                    "list_id": entry.list_id, "list_name": entry["name"], "list_country": entry.country,
                    "programme": entry.programme, "score": round(float(best[1]), 1),
                    "is_potential_match": bool(best[1] >= C.SANCTIONS_MATCH_THRESHOLD),
                    "country_matches": bool(country == entry.country),
                    "txn_ids": related.txn_id.tolist(),
                    "first_seen": related.timestamp.min().strftime("%Y-%m-%d") if len(related) else None,
                })
        hits.sort(key=lambda h: -h["score"])
        return {"threshold": C.SANCTIONS_MATCH_THRESHOLD, "screened_names": len(names), "results": hits}

    def compute_risk_indicators(self) -> dict:
        """Deterministic behavioural metrics for the alert window, compared with the customer's own history."""
        prof = self.get_customer_profile()
        expected = prof["expected_monthly_inflow_eur"] or 1.0
        win, hist = self._period("window"), self._period("history")
        hist_months = C.HISTORY_DAYS / 30

        def cash_stats(df):
            cash = df[(df.channel == "CASH_DEPOSIT") & (df.direction == "IN")]
            near = cash[(cash.amount_eur >= C.NEAR_THRESHOLD_FLOOR) & (cash.amount_eur < C.CASH_REPORTING_THRESHOLD)]
            return cash, near

        cash_w, near_w = cash_stats(win)
        cash_h, near_h = cash_stats(hist)

        # pass-through: how much of each large external credit leaves again within the window
        own = prof["customer_name"].lower()
        is_self = win.counterparty_name.str.lower().str.contains(own, regex=False) | \
            win.reference.str.lower().str.contains("own account")
        credits = win[(win.direction == "IN") & (win.amount_eur >= C.LARGE_CREDIT_MIN)]
        out_ext, out_self = win[(win.direction == "OUT") & ~is_self], win[(win.direction == "OUT") & is_self]
        passed, passed_self, pt_evidence = 0.0, 0.0, []
        for _, cr in credits.iterrows():
            lo, hi = cr.timestamp, cr.timestamp + timedelta(hours=C.PASS_THROUGH_WINDOW_HOURS)
            ext = out_ext[(out_ext.timestamp > lo) & (out_ext.timestamp <= hi)]
            slf = out_self[(out_self.timestamp > lo) & (out_self.timestamp <= hi)]
            passed += min(float(ext.amount_eur.sum()), cr.amount_eur)
            passed_self += min(float(slf.amount_eur.sum()), cr.amount_eur)
            if len(ext):
                pt_evidence += [cr.txn_id] + ext.txn_id.tolist()
        large_in = float(credits.amount_eur.sum())

        inbound_w = win[win.direction == "IN"]
        inbound_h = hist[hist.direction == "IN"]
        uniq_w = inbound_w.counterparty_name.nunique()
        uniq_h_monthly = inbound_h.counterparty_name.nunique() / hist_months
        small_in = inbound_w[(inbound_w.amount_eur < 2000) & (inbound_w.channel == "SEPA")]
        out_w = win[win.direction == "OUT"]
        crypto = out_w[out_w.counterparty_name.str.lower().str.contains("|".join(C.CRYPTO_EXCHANGE_KEYWORDS))]
        hrj = self._period("all")
        hrj = hrj[hrj.counterparty_country.isin(C.HIGH_RISK_COUNTRIES)]
        declared = {c.strip() for c in str(prof.get("declared_counterparty_countries", "")).split(",") if c.strip()}

        return {
            "customer_type": prof["customer_type"], "occupation": prof["occupation"],
            "expected_monthly_inflow_eur": expected,
            "window": {"start": self.window_start.date().isoformat(), "end": self.as_of.date().isoformat()},
            "turnover": {
                "inflow_window_eur": round(float(inbound_w.amount_eur.sum()), 2),
                "inflow_history_monthly_avg_eur": round(float(inbound_h.amount_eur.sum()) / hist_months, 2),
                "inflow_vs_expected_ratio": round(float(inbound_w.amount_eur.sum()) / expected, 2),
                "outflow_window_eur": round(float(out_w.amount_eur.sum()), 2),
                "outflow_external_window_eur": round(float(out_ext.amount_eur.sum()), 2),
                "outflow_own_account_window_eur": round(float(out_self.amount_eur.sum()), 2),
                "external_outflow_countries": sorted(set(out_ext.counterparty_country) - {"IE"}),
                "outflow_external_txn_ids": out_ext[out_ext.amount_eur >= 1000].txn_id.tolist()[:20],
            },
            "cash": {
                "deposits_window_count": int(len(cash_w)), "deposits_window_eur": round(float(cash_w.amount_eur.sum()), 2),
                "near_threshold_window_count": int(len(near_w)),
                "near_threshold_history_monthly_avg": round(len(near_h) / hist_months, 2),
                "deposits_history_monthly_avg": round(len(cash_h) / hist_months, 2),
                "near_threshold_txn_ids": near_w.txn_id.tolist(),
                "cash_txn_ids": cash_w.txn_id.tolist(),
            },
            "pass_through": {
                "large_credits_eur": round(large_in, 2), "large_credit_txn_ids": credits.txn_id.tolist(),
                "external_pass_through_ratio": round(passed / large_in, 3) if large_in else 0.0,
                "own_account_transfer_ratio": round(passed_self / large_in, 3) if large_in else 0.0,
                "window_hours": C.PASS_THROUGH_WINDOW_HOURS, "evidence_txn_ids": pt_evidence,
            },
            "counterparties": {
                "unique_inbound_window": int(uniq_w),
                "unique_inbound_history_monthly_avg": round(uniq_h_monthly, 2),
                "inbound_small_count_window": int(len(small_in)),
                "inbound_small_txn_ids": small_in.txn_id.tolist()[:30],
                "crypto_exchange_outflow_eur": round(float(crypto.amount_eur.sum()), 2),
                "crypto_outflow_share_of_inflow": round(float(crypto.amount_eur.sum()) / max(float(inbound_w.amount_eur.sum()), 1), 3),
                "crypto_txn_ids": crypto.txn_id.tolist(),
            },
            "jurisdictions": {
                "high_risk_txn_count": int(len(hrj)), "high_risk_eur": round(float(hrj.amount_eur.sum()), 2),
                "countries": sorted(hrj.counterparty_country.unique().tolist()),
                "declared_ties": sorted(declared), "high_risk_txn_ids": hrj.txn_id.tolist(),
            },
        }

    # ---------------------------------------------------------------- helpers
    def _period(self, period: str) -> pd.DataFrame:
        tx = self.data.txns(self.customer_id)
        tx = tx[tx.timestamp <= self.as_of + timedelta(days=5)]
        if period == "window":
            return tx[tx.timestamp >= self.window_start]
        if period == "history":
            return tx[(tx.timestamp >= self.history_start) & (tx.timestamp < self.window_start)]
        return tx

    def all_txn_ids(self) -> set[str]:
        return set(self.data.txns(self.customer_id).txn_id)

    def txn_lookup(self) -> dict[str, dict]:
        return self.data.txns(self.customer_id).set_index("txn_id").to_dict("index")


# JSON schemas advertised to the LLM agent (the baseline calls the methods directly)
TOOL_SPECS = [
    {"name": "get_alert", "description": "Return the alert under investigation (rule, description, lookback).",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "get_customer_profile", "description": "Return the KYC profile of the alerted customer: type, occupation or business activity, expected monthly inflow, risk rating, declared counterparty countries.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "get_transactions", "description": "List transactions for the alerted customer. Small card spend is summarised. Use period='history' to compare against the customer's normal behaviour.",
     "input_schema": {"type": "object", "properties": {
         "period": {"type": "string", "enum": ["window", "history", "all"]},
         "direction": {"type": "string", "enum": ["IN", "OUT", "ALL"]},
         "min_amount": {"type": "number"}, "limit": {"type": "integer", "maximum": 300}}}},
    {"name": "screen_counterparties", "description": "Fuzzy-screen the customer and all counterparties against the sanctions list. Returns scores, list entries, whether countries match and the linked transaction IDs.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "compute_risk_indicators", "description": "Deterministic behavioural metrics for the alert window versus the customer's own history: cash near threshold, pass-through ratios (external vs own-account), unique payers, crypto outflows, high-risk jurisdiction exposure. Includes supporting transaction IDs.",
     "input_schema": {"type": "object", "properties": {}}},
]
