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


KYC_FIELDS = ["customer_id", "customer_name", "customer_type", "customer_category", "occupation", "business_type",
              "expected_monthly_inflow_eur", "onboarding_date", "date_of_birth", "address", "residence_country",
              "nationality", "risk_rating", "declared_counterparty_countries"]
IDENTITY_FIELDS = ["customer_id", "signup_date", "signup_ip", "signup_country", "signup_device_id", "phone", "email",
                   "home_city", "account_status", "status_reason", "prior_sar"]
ADVERSE_STATUSES = {"CLOSED", "LIMITED"}
VICTIM_REASONS = ("Unauthorised", "Scam")
SANCTIONED_LOGIN_COUNTRIES = {"RU", "BY", "IR", "KP", "SY"}

# How much a shared identifier means. Devices, bank accounts and phone numbers are rarely shared by strangers;
# IP addresses often are (households, offices, mobile carrier NAT, public wi-fi, VPNs).
BASE_STRENGTH = {"device_id": "strong", "bank_fingerprint": "strong", "phone": "strong", "email": "strong",
                 "visitor_id": "medium", "ip": "weak"}
STRENGTH_RANK = {"strong": 3, "medium": 2, "weak": 1}


def _events(df):
    if df is not None:
        df["timestamp"] = pd.to_datetime(df["timestamp"], format="ISO8601")
        df = df.fillna("")
    return df


@dataclass
class CaseData:
    customers: pd.DataFrame
    transactions: pd.DataFrame
    alerts: pd.DataFrame
    sanctions: pd.DataFrame
    logins: pd.DataFrame | None = None
    instruments: pd.DataFrame | None = None
    disputes: pd.DataFrame | None = None
    events: pd.DataFrame | None = None

    @classmethod
    def load(cls, data_dir=C.DATA_DIR) -> "CaseData":
        tx = pd.read_csv(data_dir / "transactions.csv")
        tx["timestamp"] = pd.to_datetime(tx["timestamp"], format="ISO8601")

        def opt(name, **kw):
            p = data_dir / f"{name}.csv"
            return pd.read_csv(p, **kw) if p.exists() else None

        logins = opt("logins")
        if logins is not None:
            logins["timestamp"] = pd.to_datetime(logins["timestamp"], format="ISO8601")
        customers = pd.read_csv(data_dir / "customers.csv")
        for col in ("prior_sar", "in_alert_queue"):
            if col in customers:
                customers[col] = customers[col].astype(str).str.lower().eq("true")
        return cls(
            customers=customers.fillna(""),
            transactions=tx.fillna({"reference": ""}),
            alerts=pd.read_csv(data_dir / "alerts.csv"),
            sanctions=pd.read_csv(data_dir / "sanctions_list.csv"),
            logins=logins, instruments=opt("instruments"), disputes=opt("disputes"),
            events=_events(opt("account_events")),
        )

    @property
    def has_identity(self) -> bool:
        return self.logins is not None

    @cached_property
    def _tx_by_customer(self) -> dict[str, pd.DataFrame]:
        return {cid: g.sort_values("timestamp") for cid, g in self.transactions.groupby("customer_id")}

    def txns(self, customer_id: str) -> pd.DataFrame:
        return self._tx_by_customer.get(customer_id, self.transactions.iloc[0:0])

    @cached_property
    def customer_rows(self) -> dict[str, dict]:
        return self.customers.set_index("customer_id", drop=False).to_dict("index")

    @cached_property
    def logins_by_customer(self) -> dict[str, pd.DataFrame]:
        return {} if self.logins is None else {cid: g for cid, g in self.logins.groupby("customer_id")}

    @cached_property
    def link_index(self) -> dict[tuple[str, str], set[str]]:
        """(identifier type, value) -> customers that have used it."""
        idx: dict[tuple[str, str], set[str]] = {}

        def add(kind, value, cid):
            if value is not None and str(value) not in ("", "nan"):
                idx.setdefault((kind, str(value)), set()).add(cid)

        if not self.has_identity:
            return idx
        for r in self.logins[["customer_id", "ip", "device_id", "visitor_id"]].itertuples(index=False):
            add("ip", r.ip, r.customer_id)
            add("device_id", r.device_id, r.customer_id)
            add("visitor_id", r.visitor_id, r.customer_id)
        for r in self.customers[["customer_id", "phone", "email", "signup_ip", "signup_device_id"]].itertuples(index=False):
            add("phone", r.phone, r.customer_id)
            add("email", r.email, r.customer_id)
            add("ip", r.signup_ip, r.customer_id)
            add("device_id", r.signup_device_id, r.customer_id)
        if self.instruments is not None:
            for r in self.instruments[["customer_id", "fingerprint"]].itertuples(index=False):   # active and past
                add("bank_fingerprint", r.fingerprint, r.customer_id)
        return idx

    @cached_property
    def ip_types(self) -> dict[str, str]:
        if not self.has_identity:
            return {}
        return self.logins.groupby("ip").ip_type.agg(lambda s: s.mode().iat[0]).to_dict()


def _memo(fn):
    """Cache a read-only tool's result on the Toolbox (one alert), keyed by its arguments."""
    def wrapper(self, *a, **kw):
        key = (fn.__name__, a, tuple(sorted(kw.items())))
        if key not in self._cache:
            self._cache[key] = fn(self, *a, **kw)
        return self._cache[key]
    wrapper.__name__, wrapper.__doc__ = fn.__name__, fn.__doc__
    return wrapper


class Toolbox:
    """Tools bound to one alert. Agents see the alerted customer in full; for linked accounts they see only
    what an investigator's link-analysis screen shows (status, reason, what is shared), never their activity."""

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
        self._cache: dict = {}

    # ---------------------------------------------------------------- tools
    def get_alert(self) -> dict:
        """The alert as raised by the monitoring system."""
        return dict(self.alert)

    def get_customer_profile(self) -> dict:
        """KYC profile: type, occupation/business activity, expected activity, risk rating."""
        c = self._customer_row(self.customer_id)
        return {k: (float(c[k]) if k == "expected_monthly_inflow_eur" else c[k]) for k in KYC_FIELDS if k in c}

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
        if self.data.disputes is not None:
            d = self._disputes().set_index("txn_id")
            rows = rows.assign(disputed=rows.txn_id.map(lambda t: "Y" if t in d.index else "N"),
                               dispute_reason=rows.txn_id.map(lambda t: str(d.loc[t, "reason"]) if t in d.index else ""))
        return {
            "customer_id": self.customer_id, "period": period,
            "count": int(len(listed)), "truncated": bool(len(listed) > limit),
            "small_card_spend_summary": {"count": int(len(small_card)), "total_eur": round(float(small_card.amount_eur.sum()), 2)},
            "transactions": rows.drop(columns=["customer_id"]).to_dict("records"),
        }

    @_memo
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

    @_memo
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
        } | (self._identity_indicators() if self.data.has_identity else {})

    # ------------------------------------------------------------ identity, access and network tools (v2)
    def get_identity_profile(self) -> dict:
        """Sign-up details and account standing: sign-up date, IP, country and device, phone, email, status."""
        c = self._customer_row(self.customer_id)
        out = {k: c.get(k) for k in IDENTITY_FIELDS if k in c}
        out["account_age_days"] = (self.as_of - datetime.fromisoformat(str(c["signup_date"]))).days
        return out

    def get_login_history(self, limit: int = 10) -> dict:
        """Most recent logins (newest first) with IP, IP type, location, device ID (DID) and visitor ID (VID).
        Flags devices first seen inside the alert window."""
        lg = self._logins()
        first_seen = lg.groupby("device_id").timestamp.min()
        signup_dev = self._customer_row(self.customer_id).get("signup_device_id")
        rows = lg.sort_values("timestamp", ascending=False).head(max(1, min(int(limit), 100))).copy()
        rows["new_device_in_window"] = rows.device_id.map(lambda d: bool(first_seen[d] >= self.window_start and d != signup_dev))
        rows["timestamp"] = rows.timestamp.dt.strftime("%Y-%m-%d %H:%M")
        return {"customer_id": self.customer_id, "total_logins_90d": int(len(lg)),
                "distinct_devices_90d": int(lg.device_id.nunique()), "distinct_ips_90d": int(lg.ip.nunique()),
                "countries_90d": sorted(lg.country.unique().tolist()),
                "logins": rows.drop(columns=["customer_id"]).to_dict("records")}

    @_memo
    def get_linked_accounts(self) -> dict:
        """Other accounts that share an identifier with this customer (device ID, visitor ID, IP, phone, email,
        bank account / card fingerprint). Each link has a strength: IPs are weak on their own (households,
        mobile carriers, public wi-fi and VPNs are shared by many people); devices, bank accounts and phone
        numbers are strong. `accounts_sharing` is how many accounts use that identifier in total."""
        mine = self._my_identifiers()
        links: dict[str, dict] = {}
        suppressed = []
        for (kind, value), extra in mine.items():
            holders = self.data.link_index.get((kind, value), set())
            others = holders - {self.customer_id}
            if not others:
                continue
            if len(holders) > C.LINK_SUPPRESS_CARDINALITY:     # "super-node": carrier NAT, big public hotspot, VPN exit
                suppressed.append({"identifier_type": kind, "value": value, "accounts_sharing": len(holders),
                                   "ip_type": self.data.ip_types.get(value) if kind == "ip" else None})
                continue
            strength = self._link_strength(kind, value, len(holders))
            for oc in others:
                e = links.setdefault(oc, {"shared": []})
                e["shared"].append({"identifier_type": kind, "value": value, "strength": strength,
                                    "accounts_sharing": len(holders), **extra})
        out = []
        for oc, e in links.items():
            r = self._customer_row(oc)
            strongest = max(e["shared"], key=lambda x: STRENGTH_RANK[x["strength"]])["strength"]
            out.append({"customer_id": oc, "customer_name": r["customer_name"], "account_status": r["account_status"],
                        "status_reason": r["status_reason"], "prior_sar": bool(r["prior_sar"]),
                        "in_alert_queue": bool(r["in_alert_queue"]), "onboarding_date": r["onboarding_date"],
                        "is_adverse": self._is_adverse(r), "strongest_link": strongest, "shared": e["shared"]})
        out.sort(key=lambda x: (-STRENGTH_RANK[x["strongest_link"]], not x["is_adverse"], x["customer_id"]))
        return {"customer_id": self.customer_id, "linked_account_count": len(out),
                "adverse_linked_accounts": sum(1 for o in out if o["is_adverse"]), "linked_accounts": out,
                "suppressed_high_cardinality_identifiers": suppressed,
                "note": f"Identifiers shared by more than {C.LINK_SUPPRESS_CARDINALITY} accounts are suppressed as non-informative."}

    def get_disputes(self) -> dict:
        """Disputes and claims raised against this account's transactions, e.g. a payer saying a payment
        was unauthorised or a scam (a victim-funds signal), or a buyer saying goods never arrived."""
        d = self._disputes()
        return {"customer_id": self.customer_id, "count": int(len(d)),
                "disputes": d.drop(columns=["customer_id"]).to_dict("records")}

    @_memo
    def get_financials(self) -> dict:
        """Bank accounts and cards on this account, active and removed, with every other account that has
        (or had) the same bank account or card."""
        if self.data.instruments is None:
            return {"customer_id": self.customer_id, "active": [], "removed": []}
        inst = self.data.instruments.fillna("")
        mine = inst[inst.customer_id == self.customer_id]
        rows = []
        for r in mine.to_dict("records"):
            others = sorted(self.data.link_index.get(("bank_fingerprint", r["fingerprint"]), set()) - {self.customer_id})
            r["also_on_accounts"] = [{"customer_id": o, "account_status": self._customer_row(o)["account_status"],
                                      "prior_sar": bool(self._customer_row(o)["prior_sar"])} for o in others]
            rows.append({k: v for k, v in r.items() if k != "customer_id"})
        return {"customer_id": self.customer_id,
                "active": [r for r in rows if r.get("status", "ACTIVE") == "ACTIVE"],
                "removed": [r for r in rows if r.get("status") == "REMOVED"]}

    def get_account_activity(self) -> dict:
        """Non-payment account events in the last 90 days: financials added or removed, phone or email
        changed, password resets. Each event has the IP, device and country it came from."""
        e = self._events()
        if not len(e):
            return {"customer_id": self.customer_id, "events": []}
        first_seen = self._logins().groupby("device_id").timestamp.min()
        rows = e.assign(from_new_device=e.device_id.map(lambda d: bool(d in first_seen and first_seen[d] >= self.window_start)),
                        timestamp=e.timestamp.dt.strftime("%Y-%m-%d %H:%M"))
        return {"customer_id": self.customer_id, "events": rows.drop(columns=["customer_id"]).to_dict("records")}

    def _identity_indicators(self) -> dict:
        lg = self._logins()
        win = lg[lg.timestamp >= self.window_start]
        c = self._customer_row(self.customer_id)
        first_seen = lg.groupby("device_id").timestamp.min()
        new_devs = [d for d, t in first_seen.items() if t >= self.window_start and d != c.get("signup_device_id")]
        home = c.get("residence_country", "IE")
        foreign = win[win.country != home]
        vpn = win[win.ip_type == "hosting_vpn"]
        sanc = win[win.country.isin(SANCTIONED_LOGIN_COUNTRIES)]
        links = self.get_linked_accounts()["linked_accounts"]
        adverse = [l for l in links if l["is_adverse"] and STRENGTH_RANK[l["strongest_link"]] >= 2]
        d = self._disputes()
        victim = d[(d.filed_by == "payer") & d.reason.str.startswith(VICTIM_REASONS)] if len(d) else d
        victim_live = victim[~victim.status.str.contains("withdrawn", case=False)] if len(victim) else victim
        concurrent = self._adverse_device_concurrent(adverse)
        inbound_w = self._period("window")
        inbound_w = inbound_w[inbound_w.direction == "IN"]
        return {
            "access": {
                "logins_window": int(len(win)), "distinct_devices_window": int(win.device_id.nunique()),
                "new_devices_window": new_devs,
                "new_device_login_share_window": round(float(win.device_id.isin(new_devs).mean()), 3) if len(win) else 0.0,
                "foreign_login_countries_window": sorted(foreign.country.unique().tolist()),
                "foreign_login_share_window": round(len(foreign) / len(win), 3) if len(win) else 0.0,
                "vpn_hosting_login_share_window": round(len(vpn) / len(win), 3) if len(win) else 0.0,
                "sanctioned_country_logins_window": int(len(sanc)),
                "evidence_login_ids": (win[win.device_id.isin(new_devs)].login_id.tolist() + foreign.login_id.tolist())[:15],
            },
            "network": {
                "linked_accounts": len(links),
                "strong_links": sum(1 for l in links if l["strongest_link"] == "strong"),
                "adverse_links_medium_or_strong": len(adverse),
                "adverse_linked_ids": [l["customer_id"] for l in adverse],
                "adverse_with_prior_sar": sum(1 for l in adverse if l["prior_sar"]),
                "adverse_typologies": sorted({self._customer_row(l["customer_id"]).get("status_typology") for l in adverse} - {"", None}),
                "adverse_shared_identifier_types": sorted({s["identifier_type"] for l in adverse for s in l["shared"]
                                                          if STRENGTH_RANK[s["strength"]] >= 2}),
                "weak_only_links": sum(1 for l in links if l["strongest_link"] == "weak"),
                "adverse_device_used_concurrently": concurrent,
                "clean_medium_links": sum(1 for l in links if l["strongest_link"] == "medium" and not l["is_adverse"]),
            },
            "activity": self._activity_indicators(),
            "disputes": {
                "total": int(len(d)), "victim_claims": int(len(victim)),
                "victim_claims_not_withdrawn": int(len(victim_live)),
                "victim_claim_share_of_inbound_window": round(len(victim) / max(len(inbound_w), 1), 3),
                "buyer_claims": int((d.filed_by == "buyer").sum()) if len(d) else 0,
                "open": int(d.status.str.startswith("Open").sum()) if len(d) else 0,
                "victim_claim_ids": victim.dispute_id.tolist() if len(d) else [],
                "victim_claim_txn_ids": victim.txn_id.tolist() if len(d) else [],
            },
        }

    def _adverse_device_concurrent(self, adverse: list[dict]) -> bool:
        """Did a closed/limited account use a shared device during this customer's alert window (a live shared
        device), rather than months earlier (for example a second-hand phone)?"""
        lg = self.data.logins
        for l in adverse:
            for sh in l["shared"]:
                if sh["identifier_type"] in ("device_id", "visitor_id"):
                    col = "device_id" if sh["identifier_type"] == "device_id" else "visitor_id"
                    other = lg[(lg.customer_id == l["customer_id"]) & (lg[col] == sh["value"])]
                    if (other.timestamp >= self.window_start).any():
                        return True
        return False

    def _activity_indicators(self) -> dict:
        e = self._events()
        w = e[e.timestamp >= self.window_start] if len(e) else e
        home = self._customer_row(self.customer_id).get("residence_country", "IE")
        first_seen = self._logins().groupby("device_id").timestamp.min()
        new_dev = {d for d, t in first_seen.items() if t >= self.window_start}
        sensitive = w[w.event_type.isin(["PASSWORD_RESET", "PHONE_CHANGED", "EMAIL_CHANGED"])] if len(w) else w
        fin_added = w[w.event_type == "FINANCIAL_ADDED"] if len(w) else w
        fin_removed = w[w.event_type == "FINANCIAL_REMOVED"] if len(w) else w
        removed_after_use = fin_removed[fin_removed.details.str.contains("after withdrawals")] if len(w) else w
        risky = w[w.device_id.isin(new_dev) | (w.country.astype(str).ne(home) & w.country.astype(str).ne(""))] if len(w) else w
        return {
            "events_window": int(len(w)),
            "credential_or_contact_changes_window": int(len(sensitive)),
            "financials_added_window": int(len(fin_added)),
            "financials_removed_window": int(len(fin_removed)),
            "financial_added_then_removed_window": bool(len(fin_added) and len(removed_after_use)),
            "events_from_new_device_or_abroad": int(len(risky)),
            "risky_event_ids": risky.event_id.tolist() if len(w) else [],
        }

    def _events(self) -> pd.DataFrame:
        if self.data.events is None:
            return pd.DataFrame(columns=["event_id", "customer_id", "timestamp", "event_type", "details", "ip", "device_id", "country"])
        e = self.data.events
        return e[(e.customer_id == self.customer_id) & (e.timestamp <= self.as_of)]

    def _my_identifiers(self) -> dict[tuple[str, str], dict]:
        """This customer's identifiers, with the login IDs that show each one being used."""
        ids: dict[tuple[str, str], dict] = {}
        lg = self._logins()
        for kind in ("ip", "device_id", "visitor_id"):
            for value, g in lg.groupby(kind):
                ids[(kind, str(value))] = {"ip_type": self.data.ip_types.get(value)} if kind == "ip" else {}
                ids[(kind, str(value))]["evidence_login_ids"] = g.sort_values("timestamp").login_id.tolist()[-3:]
        c = self._customer_row(self.customer_id)
        for kind, col in (("phone", "phone"), ("email", "email"), ("device_id", "signup_device_id"), ("ip", "signup_ip")):
            if c.get(col):
                ids.setdefault((kind, str(c[col])), {"source": "sign-up" if col.startswith("signup") else "profile"})
        if self.data.instruments is not None:
            for r in self.data.instruments[self.data.instruments.customer_id == self.customer_id].itertuples():
                ids[("bank_fingerprint", r.fingerprint)] = {"instrument": f"{r.type} {r.masked} ({r.bank_country})",
                                                            "instrument_id": r.instrument_id}
        return ids

    def _link_strength(self, kind: str, value: str, cardinality: int) -> str:
        s = BASE_STRENGTH[kind]
        if kind == "ip" and self.data.ip_types.get(value) == "residential" and cardinality <= 4:
            s = "medium"                                  # a small shared household or office
        if cardinality > 10 and s != "weak":
            s = "medium" if s == "strong" else "weak"     # identifiers shared by many accounts carry less signal
        return s

    @staticmethod
    def _is_adverse(r) -> bool:
        return r.get("account_status") in ADVERSE_STATUSES or bool(r.get("prior_sar"))

    def _customer_row(self, cid: str) -> dict:
        return self.data.customer_rows[cid]

    def _logins(self) -> pd.DataFrame:
        if not self.data.has_identity:
            return pd.DataFrame(columns=["login_id", "customer_id", "timestamp", "ip", "ip_type", "country", "device_id", "visitor_id"])
        lg = self.data.logins_by_customer.get(self.customer_id, self.data.logins.iloc[0:0])
        return lg[lg.timestamp <= self.as_of]

    def _disputes(self) -> pd.DataFrame:
        if self.data.disputes is None:
            return pd.DataFrame(columns=["dispute_id", "customer_id", "txn_id", "reason", "status", "filed_by"])
        return self.data.disputes[self.data.disputes.customer_id == self.customer_id]

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

    def all_evidence_ids(self) -> set[str]:
        """Every ID the agent may legitimately cite: its transactions, logins, disputes, and linked accounts."""
        ids = self.all_txn_ids() | {self.customer_id}
        if self.data.has_identity:
            ids |= set(self._logins().login_id) | set(self._disputes().dispute_id) | set(self._events().event_id)
            ids |= {l["customer_id"] for l in self.get_linked_accounts()["linked_accounts"]}
        return ids

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
    {"name": "get_identity_profile", "description": "Sign-up date, sign-up IP, country and device, phone, email, account age, account status and whether a SAR was filed before.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "get_login_history", "description": "Most recent logins, newest first: IP, IP type (residential, mobile, public_wifi, hosting_vpn), city, country, device ID (DID), visitor ID (VID) and whether the device is new in the alert window.",
     "input_schema": {"type": "object", "properties": {"limit": {"type": "integer", "maximum": 100}}}},
    {"name": "get_linked_accounts", "description": "Accounts sharing an identifier with this customer (device, visitor ID, IP, phone, email, bank account or card fingerprint), with link strength, how many accounts share it, and each linked account's status, status reason and prior SAR flag.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "get_financials", "description": "Bank accounts and cards on this account, active and removed (with dates), and every other account that has or had the same bank account or card, with that account's status and prior-SAR flag.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "get_account_activity", "description": "Account events in the last 90 days: financial added/removed, phone or email changed, password reset. Each event has its IP, device, country and whether the device is new in the alert window.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "get_disputes", "description": "Disputes raised on this account's transactions: who filed (payer or buyer), reason (unauthorised, scam, item not received), amount, status.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "compute_risk_indicators", "description": "Deterministic behavioural metrics for the alert window versus the customer's own history: cash near threshold, pass-through ratios (external vs own-account), unique payers, crypto outflows, high-risk jurisdiction exposure; plus access (new devices, foreign and VPN logins), network (links to closed or limited accounts) and dispute metrics. Includes supporting IDs.",
     "input_schema": {"type": "object", "properties": {}}},
]
