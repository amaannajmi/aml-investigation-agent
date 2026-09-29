"""v3 enrichment: full profile, past financials, account activity events and expected account actions.

Runs after the identity layer with its own random stream, so transactions (v1)
and logins/links (v2) are left exactly as they were.

* customers        + date_of_birth, address, customer_category (Customer / Merchant), business_type
* instruments      + status (ACTIVE / REMOVED), removed_date
* account_events   financial added/removed, phone/email changed, password reset: with IP and device
* ground_truth     + expected_action: what should happen to the account

Every address is fictional ("Example" streets, reserved Eircode-like codes).
"""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd

AS_OF = datetime(2026, 9, 1, 9, 0)
WINDOW_START = AS_OF - timedelta(days=30)
START = AS_OF - timedelta(days=90)

STREETS = ["Example Street", "Sample Road", "Placeholder Avenue", "Demo Lane", "Fictional Terrace", "Test Park"]
BUSINESS_TYPES = {  # occupation keyword -> business type shown on the profile
    "restaurant": "Food service", "takeaway": "Food service", "convenience": "Retail", "online retail": "E-commerce",
    "consulting": "Professional services", "freight": "Logistics", "auto parts": "Wholesale trade",
    "engineering": "Professional services", "metals": "Commodities trading", "industrial": "Wholesale trade",
}

# What should happen to the account, by scenario. Used to score the agent's account-action recommendation.
EXPECTED_ACTION = {
    "money_mule": "CLOSE_ACCOUNT", "mule_cash_out": "CLOSE_ACCOUNT", "pass_through": "CLOSE_ACCOUNT",
    "sanctions_exposure": "RESTRICT", "sanctions_transliteration": "RESTRICT",
    "structuring": "REQUEST_DOCUMENTS", "high_risk_jurisdiction": "REQUEST_DOCUMENTS",
}
ACTIONS = ("NO_ACTION", "REQUEST_DOCUMENTS", "RESTRICT", "CLOSE_ACCOUNT")


def enrich(frames: dict[str, pd.DataFrame], seed: int) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed + 2000)
    cust = frames["customers"].copy()
    inst = frames["instruments"].copy()
    logins = frames["logins"]
    truth = frames["ground_truth"].copy()
    alerts = frames["alerts"]

    # --- profile ---------------------------------------------------------------------------------------
    def dob(row):
        if row.customer_type == "Business":
            return ""
        occ = str(row.occupation).lower()
        age = int(rng.integers(19, 24)) if "student" in occ else int(rng.integers(24, 67))
        return (AS_OF - timedelta(days=age * 365 + int(rng.integers(0, 365)))).date().isoformat()

    def business_type(row):
        occ = str(row.occupation).lower()
        return next((v for k, v in BUSINESS_TYPES.items() if k in occ), "Other") if row.customer_type == "Business" else ""

    cust["date_of_birth"] = [dob(r) for r in cust.itertuples()]
    cust["address"] = [f"{int(rng.integers(1, 180))} {rng.choice(STREETS)}, {r.home_city or 'Dublin'}, "
                       f"X{int(rng.integers(10, 99))} E{int(rng.integers(100, 999))}" for r in cust.itertuples()]
    cust["customer_category"] = np.where(cust.customer_type == "Business", "Merchant", "Customer")
    cust["business_type"] = [business_type(r) for r in cust.itertuples()]

    # --- past financials ---------------------------------------------------------------------------------
    inst["status"], inst["removed_date"] = "ACTIVE", ""
    t = truth.merge(alerts[["alert_id", "customer_id"]], on="alert_id")
    scen = dict(zip(t.customer_id, t.scenario))
    events = []

    def ev(cid, when, etype, details, login=None):
        events.append({"event_id": f"EVT-{len(events) + 1:05d}", "customer_id": cid, "timestamp": when.replace(microsecond=0).isoformat(sep=" "),
                       "event_type": etype, "details": details,
                       "ip": login.ip if login is not None else "", "device_id": login.device_id if login is not None else "",
                       "country": login.country if login is not None else ""})

    lg_by = {cid: g.sort_values("timestamp") for cid, g in logins.assign(ts=pd.to_datetime(logins.timestamp)).groupby("customer_id")}

    def login_near(cid, when, prefer_new=False):
        g = lg_by.get(cid)
        if g is None or g.empty:
            return None
        if prefer_new:
            first = g.groupby("device_id").ts.transform("min")
            g2 = g[first >= WINDOW_START]
            if len(g2):
                g = g2
        idx = (g.ts - pd.Timestamp(when)).abs().idxmin()
        return g.loc[idx]

    # removal of the shared foreign withdrawal account right after the cash-out (mules), expired cards (benign)
    for i, r in inst.iterrows():
        sc = scen.get(r.customer_id)
        if sc in ("money_mule", "mule_cash_out") and r.bank_country == "LT":
            when = AS_OF - timedelta(days=int(rng.integers(1, 4)), hours=int(rng.integers(0, 12)))
            inst.at[i, "status"], inst.at[i, "removed_date"] = "REMOVED", when.date().isoformat()
    extra = []
    for cid in cust.loc[cust.in_alert_queue.astype(bool), "customer_id"]:
        if rng.random() < 0.2:
            when = START + timedelta(days=int(rng.integers(0, 50)))
            extra.append({"instrument_id": f"FI-9{len(extra) + 1:04d}", "customer_id": cid, "type": "card",
                          "masked": f"Visa **** {int(rng.integers(1000, 9999))}", "bank_country": "IE",
                          "fingerprint": f"BKF-{''.join(rng.choice(list('0123456789abcdef'), 12))}",
                          "added_date": (when - timedelta(days=int(rng.integers(400, 1200)))).date().isoformat(),
                          "status": "REMOVED", "removed_date": when.date().isoformat()})
            ev(cid, when, "FINANCIAL_REMOVED", f"Card {extra[-1]['masked']} removed (expired)", login_near(cid, when))
    inst = pd.concat([inst, pd.DataFrame(extra)], ignore_index=True)

    # added-financial events inside the 90 days
    for r in inst.itertuples():
        added = datetime.fromisoformat(str(r.added_date))
        if added >= START and r.customer_id in scen:
            ev(r.customer_id, added + timedelta(hours=int(rng.integers(8, 22))), "FINANCIAL_ADDED",
               f"{r.type.replace('_', ' ')} {r.masked} ({r.bank_country}) added", login_near(r.customer_id, added, prefer_new=True))
        if r.status == "REMOVED" and r.bank_country == "LT":
            rem = datetime.fromisoformat(str(r.removed_date)) + timedelta(hours=int(rng.integers(8, 22)))
            ev(r.customer_id, rem, "FINANCIAL_REMOVED", f"{r.type.replace('_', ' ')} {r.masked} ({r.bank_country}) removed "
               f"after withdrawals", login_near(r.customer_id, rem, prefer_new=True))

    # account takeover / sale pattern: password reset and contact change from the new device
    for cid, sc in scen.items():
        if sc in ("money_mule", "mule_cash_out", "pass_through"):
            g = lg_by.get(cid)
            if g is None:
                continue
            first = g.groupby("device_id").ts.min()
            new = first[first >= WINDOW_START]
            if len(new):
                t0 = new.min().to_pydatetime()
                lg = login_near(cid, t0, prefer_new=True)
                ev(cid, t0 + timedelta(minutes=int(rng.integers(3, 40))), "PASSWORD_RESET", "Password reset via email link", lg)
                ev(cid, t0 + timedelta(hours=int(rng.integers(1, 30))), "PHONE_CHANGED",
                   "Phone number changed on the profile (2FA moved to new number)", lg)
        elif rng.random() < 0.12:          # ordinary housekeeping
            when = START + timedelta(days=int(rng.integers(0, 85)))
            etype = str(rng.choice(["PASSWORD_RESET", "EMAIL_CHANGED"]))
            ev(cid, when, etype, "Password reset via email link" if etype == "PASSWORD_RESET" else "Email address updated",
               login_near(cid, when))

    truth["expected_action"] = truth.scenario.map(EXPECTED_ACTION).fillna("NO_ACTION")
    events_df = pd.DataFrame(events).sort_values(["customer_id", "timestamp"]).reset_index(drop=True)
    events_df["event_id"] = [f"EVT-{i + 1:05d}" for i in range(len(events_df))]
    return frames | {"customers": cust, "instruments": inst, "account_events": events_df, "ground_truth": truth}
