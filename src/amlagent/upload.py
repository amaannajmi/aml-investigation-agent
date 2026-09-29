"""Bring your own data: validate uploaded CSVs (or a .zip of them) and turn them into a case folder.

Only transactions.csv is strictly required. Everything else is optional and filled with safe defaults, so
you can start with a transaction export and add logins, financials, disputes and account events later:

  transactions.csv   txn_id, customer_id, timestamp, direction (IN/OUT), amount_eur, counterparty_name
                     [channel, counterparty_country, reference]
  customers.csv      customer_id [customer_name, customer_type, occupation, expected_monthly_inflow_eur, ...]
  alerts.csv         alert_id, customer_id [rule_id, rule_name, rule_description, alert_date, lookback_days]
                     (missing: one "manual review" alert per customer)
  logins.csv         customer_id, timestamp, ip, device_id [login_id, visitor_id, ip_type, city, country, latitude, longitude]
  instruments.csv    customer_id, fingerprint [instrument_id, type, masked, bank_country, added_date, status, removed_date]
  disputes.csv       customer_id, txn_id, reason [dispute_id, opened, amount_eur, status, filed_by, counterparty_name]
  account_events.csv customer_id, timestamp, event_type [event_id, details, ip, device_id, country]
  sanctions_list.csv name, country [list_id, party_type, programme]    (missing: the demo list)
  ground_truth.csv   alert_id, is_suspicious [typology, scenario, variant, expected_action]  (enables evaluation)
"""
from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path

import pandas as pd

from . import config as C

REQUIRED = {
    "transactions": ["txn_id", "customer_id", "timestamp", "direction", "amount_eur", "counterparty_name"],
    "customers": ["customer_id"],
    "alerts": ["alert_id", "customer_id"],
    "logins": ["customer_id", "timestamp", "ip", "device_id"],
    "instruments": ["customer_id", "fingerprint"],
    "disputes": ["customer_id", "txn_id", "reason"],
    "account_events": ["customer_id", "timestamp", "event_type"],
    "sanctions_list": ["name", "country"],
    "ground_truth": ["alert_id", "is_suspicious"],
}
DEFAULTS = {
    "transactions": {"channel": "UNKNOWN", "counterparty_country": "", "reference": ""},
    "customers": {"customer_type": "Individual", "occupation": "Not recorded", "onboarding_date": "", "residence_country": "IE",
                  "nationality": "", "risk_rating": "Not rated", "declared_counterparty_countries": "", "business_type": "",
                  "date_of_birth": "", "address": "", "in_alert_queue": True, "phone": "", "email": "", "signup_ip": "",
                  "signup_country": "", "signup_device_id": "", "home_city": "", "account_status": "ACTIVE",
                  "status_reason": "", "prior_sar": False, "status_typology": ""},
    "alerts": {"rule_id": "MANUAL", "rule_name": "Manual review", "rule_description": "Uploaded for review", "lookback_days": 30},
    "logins": {"visitor_id": "", "ip_type": "unknown", "city": "", "country": "", "latitude": None, "longitude": None,
               "user_agent": "", "success": True},
    "instruments": {"type": "bank_account", "masked": "", "bank_country": "", "added_date": "", "status": "ACTIVE", "removed_date": ""},
    "disputes": {"opened": "", "amount_eur": 0.0, "status": "Open", "filed_by": "payer", "counterparty_name": ""},
    "account_events": {"details": "", "ip": "", "device_id": "", "country": ""},
    "sanctions_list": {"party_type": "", "programme": "UPLOADED"},
    "ground_truth": {"typology": "", "scenario": "uploaded", "variant": "", "expected_action": "NO_ACTION"},
}
ID_PREFIX = {"logins": ("login_id", "LGN"), "instruments": ("instrument_id", "FI"), "disputes": ("dispute_id", "DSP"),
             "account_events": ("event_id", "EVT"), "sanctions_list": ("list_id", "SL")}


def read_uploads(files: dict[str, bytes]) -> dict[str, pd.DataFrame]:
    """files: {filename: bytes}. Accepts CSVs and/or .zip archives of CSVs."""
    out = {}
    for name, blob in files.items():
        if name.lower().endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(blob)) as z:
                for n in z.namelist():
                    if n.lower().endswith(".csv") and not n.startswith("__MACOSX"):
                        out[Path(n).stem.lower()] = pd.read_csv(z.open(n))
        elif name.lower().endswith(".csv"):
            out[Path(name).stem.lower()] = pd.read_csv(io.BytesIO(blob))
    return out


def prepare(frames: dict[str, pd.DataFrame]) -> tuple[dict[str, pd.DataFrame], list[str], list[str]]:
    """Validate and complete uploaded tables. Returns (tables, errors, notes)."""
    errors, notes = [], []
    unknown = sorted(set(frames) - set(REQUIRED))
    if unknown:
        notes.append(f"Ignored files: {', '.join(f + '.csv' for f in unknown)}")
    if "transactions" not in frames:
        return {}, ["transactions.csv is required."], notes
    t = {k: v.copy() for k, v in frames.items() if k in REQUIRED}
    for name, df in t.items():
        missing = [c for c in REQUIRED[name] if c not in df.columns]
        if missing:
            errors.append(f"{name}.csv is missing required column(s): {', '.join(missing)}")
    if errors:
        return {}, errors, notes

    tx = t["transactions"]
    tx["direction"] = tx.direction.astype(str).str.upper().str.strip()
    bad_dir = ~tx.direction.isin(["IN", "OUT"])
    if bad_dir.any():
        errors.append(f"transactions.csv: direction must be IN or OUT ({int(bad_dir.sum())} rows are not)")
    try:
        ts = pd.to_datetime(tx.timestamp, format="mixed")
        tx["timestamp"] = ts.dt.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        errors.append("transactions.csv: timestamp could not be read as a date/time")
    if pd.to_numeric(tx.amount_eur, errors="coerce").isna().any():
        errors.append("transactions.csv: amount_eur must be numeric")
    if tx.txn_id.duplicated().any():
        errors.append("transactions.csv: txn_id values must be unique")
    if errors:
        return {}, errors, notes

    # customers: derive from transactions if absent, fill optional fields
    if "customers" not in t:
        t["customers"] = pd.DataFrame({"customer_id": sorted(tx.customer_id.unique())})
        notes.append("No customers.csv: created a minimal profile per customer from the transactions.")
    cu = t["customers"]
    if "customer_name" not in cu:
        cu["customer_name"] = cu.customer_id
    if "expected_monthly_inflow_eur" not in cu:
        months = max((pd.to_datetime(tx.timestamp).max() - pd.to_datetime(tx.timestamp).min()).days / 30, 1)
        inflow = tx[tx.direction == "IN"].groupby("customer_id").amount_eur.sum() / months
        cu["expected_monthly_inflow_eur"] = cu.customer_id.map(inflow).fillna(0).round(2)
        notes.append("No expected_monthly_inflow_eur: used each customer's average monthly inflow as a stand-in.")
    for name, df in t.items():
        for col, val in DEFAULTS.get(name, {}).items():
            if col not in df.columns:
                df[col] = val
    cu["customer_category"] = cu.get("customer_category", pd.Series(dtype=str)).fillna("") \
        if "customer_category" in cu else ""
    cu.loc[cu.customer_category.eq("") | cu.customer_category.isna(), "customer_category"] = \
        cu.customer_type.map(lambda x: "Merchant" if str(x).lower() == "business" else "Customer")
    if "signup_date" not in cu:
        cu["signup_date"] = cu.onboarding_date
    unknown_cust = set(tx.customer_id) - set(cu.customer_id)
    if unknown_cust:
        errors.append(f"transactions.csv has {len(unknown_cust)} customer_id(s) not in customers.csv")

    # alerts: one manual-review alert per customer if none supplied
    if "alerts" not in t:
        last = pd.to_datetime(tx.timestamp).max().date().isoformat()
        t["alerts"] = pd.DataFrame({"alert_id": [f"REV-{i + 1:04d}" for i in range(len(cu))], "customer_id": cu.customer_id,
                                    **{k: v for k, v in DEFAULTS["alerts"].items()}, "alert_date": last})
        notes.append("No alerts.csv: created one manual-review alert per customer, dated at the last transaction.")
    al = t["alerts"]
    if "alert_date" not in al:
        al["alert_date"] = pd.to_datetime(tx.timestamp).max().date().isoformat()
    al["alert_date"] = pd.to_datetime(al.alert_date, format="mixed").dt.date.astype(str)

    # generated IDs, identity completeness
    for name, (col, prefix) in ID_PREFIX.items():
        if name in t and col not in t[name]:
            t[name].insert(0, col, [f"{prefix}-U{i + 1:05d}" for i in range(len(t[name]))])
    if "logins" in t:
        if "instruments" not in t:
            t["instruments"] = pd.DataFrame(columns=["instrument_id"] + REQUIRED["instruments"] + list(DEFAULTS["instruments"]))
        if "disputes" not in t:
            t["disputes"] = pd.DataFrame(columns=["dispute_id"] + REQUIRED["disputes"] + list(DEFAULTS["disputes"]))
    if "sanctions_list" not in t:
        from .data_gen import SANCTIONS
        t["sanctions_list"] = pd.DataFrame(SANCTIONS, columns=["list_id", "name", "party_type", "country", "programme"])
        notes.append("No sanctions_list.csv: using the demo (fictional) sanctions list.")
    if "ground_truth" in t and "expected_action" in t["ground_truth"]:
        pass
    return t, errors, notes


def save(tables: dict[str, pd.DataFrame]) -> Path:
    blob = b"".join(df.to_csv(index=False).encode() for _, df in sorted(tables.items()))
    folder = C.ROOT / "uploads" / hashlib.sha1(blob).hexdigest()[:12]
    folder.mkdir(parents=True, exist_ok=True)
    for name, df in tables.items():
        df.to_csv(folder / f"{name}.csv", index=False)
    return folder


def template_zip(n_customers: int = 3) -> bytes:
    """A small example of every file, cut from the demo data, to show the expected format."""
    buf = io.BytesIO()
    alerts = pd.read_csv(C.DATA_DIR / "alerts.csv").head(n_customers)
    ids = set(alerts.customer_id)
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name in REQUIRED:
            p = C.DATA_DIR / f"{name}.csv"
            if not p.exists():
                continue
            df = pd.read_csv(p)
            if "customer_id" in df:
                df = df[df.customer_id.isin(ids)]
            elif "alert_id" in df:
                df = df[df.alert_id.isin(alerts.alert_id)]
            z.writestr(f"{name}.csv", df.to_csv(index=False))
        z.writestr("README.txt", __doc__)
    return buf.getvalue()
