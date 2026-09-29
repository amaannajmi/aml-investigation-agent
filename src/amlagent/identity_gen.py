"""Identity, access and network layer (v2).

Adds what an investigator sees on the account-overview screens, on top of the
v1 transaction data (which it leaves untouched, using its own random stream):

* customers        + signup date/IP/device/country, phone, email, account status, prior SAR
* logins.csv       login events: time, IP, IP type, city/country, lat/lon, device ID (DID), visitor ID (VID)
* instruments.csv  bank accounts and cards on file, with a fingerprint shared across accounts
* disputes.csv     claims against the account (e.g. payer says "unauthorised" or "scam")

It also creates "network" accounts that are not in the alert queue but are
linked to alerted customers: previously closed mule accounts that share a
device or withdrawal bank account, and ordinary people (flatmates, staff)
who share a home or office IP. Some links are evidence; most are not.

Every identifier is synthetic: IPs come from reserved documentation /
benchmark ranges, phone numbers from the UK's reserved drama range
(+44 7700 900xxx), emails use example.com/.org/.net.
"""
from __future__ import annotations

import ipaddress
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

AS_OF = datetime(2026, 9, 1, 9, 0)
WINDOW_START = AS_OF - timedelta(days=30)
START = AS_OF - timedelta(days=90)

IE_CITIES = [("Dublin", 53.35, -6.26), ("Cork", 51.90, -8.47), ("Galway", 53.27, -9.05), ("Limerick", 52.66, -8.63)]
EU_TRAVEL = [("Lisbon", "PT", 38.72, -9.14), ("Barcelona", "ES", 41.39, 2.17), ("Paris", "FR", 48.86, 2.35),
             ("Berlin", "DE", 52.52, 13.40), ("London", "GB", 51.51, -0.13)]
CONTROLLER_LOCS = [("Bucharest", "RO", 44.43, 26.10), ("Sofia", "BG", 42.70, 23.32), ("Vilnius", "LT", 54.69, 25.28),
                   ("Riga", "LV", 56.95, 24.11), ("Lagos", "NG", 6.52, 3.38), ("Istanbul", "TR", 41.01, 28.98)]
OFFSHORE_LOCS = [("Nicosia", "CY", 35.19, 33.38), ("Dubai", "AE", 25.20, 55.27), ("Hong Kong", "HK", 22.32, 114.17)]
SANCTIONED_LOCS = [("Moscow", "RU", 55.76, 37.62), ("Minsk", "BY", 53.90, 27.56)]
HOSTING = [("Amsterdam", "NL", 52.37, 4.90), ("Frankfurt", "DE", 50.11, 8.68)]

MULE_REASONS = ["Closed: money mule activity, SAR filed", "Limited: multiple unauthorised-funds complaints",
                "Closed: linked to mule network (law enforcement request)"]
PT_REASONS = ["Closed: rapid movement of funds, SAR filed", "Closed: KYC documents found to be forged"]


class IdentityBuilder:
    def __init__(self, frames: dict[str, pd.DataFrame], seed: int):
        self.rng = np.random.default_rng(seed + 1000)
        self.f = frames
        self.customers = frames["customers"].copy()
        self.truth = frames["ground_truth"].merge(frames["alerts"][["alert_id", "customer_id"]], on="alert_id")
        self.tx = frames["transactions"]
        self.logins, self.instruments, self.disputes, self.extra_customers = [], [], [], []
        self.identity = {}                                  # customer_id -> identity fields
        # address pools (all reserved ranges)
        self._res = iter(ipaddress.ip_network("198.18.0.0/15").hosts())            # residential / office
        self._hosting = [str(ip) for ip in list(ipaddress.ip_network("192.0.2.0/24").hosts())[:40]]
        self._mobile = [str(ipaddress.ip_address("100.64.0.0") + int(i) * 257) for i in range(30)]   # carrier NAT
        self._public_wifi = [str(ip) for ip in list(ipaddress.ip_network("203.0.113.0/24").hosts())[:6]]
        self._phones = iter(self.rng.permutation(1000))
        self._seq = {"LGN": 0, "DSP": 0, "FI": 0, "CUS": 90000}

    # ---------------------------------------------------------------- ids
    def _id(self, kind):
        self._seq[kind] += 1
        return {"LGN": f"LGN-{self._seq[kind]:06d}", "DSP": f"DSP-{self._seq[kind]:05d}",
                "FI": f"FI-{self._seq[kind]:05d}", "CUS": f"CUS-{self._seq[kind]}"}[kind]

    def _hex(self, n=10):
        return "".join(self.rng.choice(list("0123456789abcdef"), n))

    def device(self):
        return {"did": f"DID-{self._hex()}", "vid": f"VID-{self._hex()}",
                "ua": str(self.rng.choice(["iOS 19 / Safari", "Android 16 / Chrome", "Windows 11 / Edge", "macOS / Chrome", "Android 15 / App"]))}

    def res_ip(self):
        return str(next(self._res))

    def ts(self, a, b):
        return (a + timedelta(seconds=float(self.rng.uniform(0, (b - a).total_seconds())))).replace(microsecond=0)

    # ---------------------------------------------------------------- identity
    def base_identity(self, cid, name, onboarding, **over):
        city = IE_CITIES[int(self.rng.integers(0, len(IE_CITIES)))]
        dev = self.device()
        first, *rest = name.replace(" Ltd", "").split(" ")
        idn = {
            "customer_id": cid, "home_city": city, "home_ip": self.res_ip(), "devices": [dev],
            "phone": f"+44 7700 900{next(self._phones):03d}",
            "email": f"{first.lower()}.{(rest[0] if rest else 'mail').lower()}{int(self.rng.integers(1, 99))}@example.{self.rng.choice(['com', 'org', 'net'])}",
            "signup_date": onboarding, "signup_ip": None, "signup_device": dev["did"], "signup_country": "IE",
            "account_status": "ACTIVE", "status_reason": "", "prior_sar": False, "status_typology": "",
        }
        idn["signup_ip"] = idn["home_ip"]
        idn.update(over)
        self.identity[cid] = idn
        return idn

    def login(self, cid, when, ip, ip_type, loc, dev, success=True):
        city, country, lat, lon = loc
        self.logins.append({"login_id": self._id("LGN"), "customer_id": cid, "timestamp": when.isoformat(sep=" "),
                            "ip": ip, "ip_type": ip_type, "city": city, "country": country,
                            "latitude": round(lat + self.rng.normal(0, 0.03), 4), "longitude": round(lon + self.rng.normal(0, 0.03), 4),
                            "device_id": dev["did"], "visitor_id": dev["vid"], "user_agent": dev["ua"], "success": success})

    def normal_logins(self, idn, start=START, n=None, end=AS_OF):
        city, lat, lon = idn["home_city"]
        home = (city, "IE", lat, lon)
        n = n or int(self.rng.integers(12, 26))
        for _ in range(n):
            r = self.rng.random()
            dev = idn["devices"][int(self.rng.integers(0, len(idn["devices"])))]
            when = self.ts(start, end)
            if r < 0.65:
                self.login(idn["customer_id"], when, idn["home_ip"], "residential", home, dev)
            elif r < 0.93:
                self.login(idn["customer_id"], when, str(self.rng.choice(self._mobile)), "mobile", home, dev)
            else:
                c, cc, la, lo = EU_TRAVEL[int(self.rng.integers(0, len(EU_TRAVEL)))]
                self.login(idn["customer_id"], when, self.res_ip(), "residential", (c, cc, la, lo), dev)
        if self.rng.random() < 0.3:     # the odd mistyped password
            self.login(idn["customer_id"], self.ts(start, end), idn["home_ip"], "residential", home, idn["devices"][0], success=False)

    def instrument(self, cid, kind="bank_account", fingerprint=None, country="IE", added=None, label=None):
        fp = fingerprint or f"BKF-{self._hex(12)}"
        self.instruments.append({"instrument_id": self._id("FI"), "customer_id": cid, "type": kind,
                                 "masked": label or (f"IE** **** **** {int(self.rng.integers(1000, 9999))}" if kind == "bank_account"
                                                     else f"Visa **** {int(self.rng.integers(1000, 9999))}"),
                                 "bank_country": country, "fingerprint": fp,
                                 "added_date": (added or self.ts(START - timedelta(days=400), START)).date().isoformat()})
        return fp

    def dispute(self, cid, txn, reason, status, filed_by):
        self.disputes.append({"dispute_id": self._id("DSP"), "customer_id": cid, "txn_id": txn["txn_id"],
                              "opened": min(pd.Timestamp(txn["timestamp"]) + timedelta(days=int(self.rng.integers(1, 9))),
                                            pd.Timestamp(AS_OF)).date().isoformat(),
                              "reason": reason, "amount_eur": txn["amount_eur"], "status": status,
                              "filed_by": filed_by, "counterparty_name": txn["counterparty_name"]})

    def network_account(self, name_hint, status, reason, prior_sar, typology, onboarding_days):
        cid = self._id("CUS")
        first = ["Alex", "Sam", "Jordan", "Robin", "Chris", "Dara", "Kim", "Ali", "Luca", "Nina", "Ola", "Ren"][int(self.rng.integers(0, 12))]
        last = ["Byrne", "Novak", "Silva", "Kowalski", "Popescu", "Ivanov", "Dimitrov", "Kaya", "Moreno", "Walsh"][int(self.rng.integers(0, 10))]
        name = f"{first} {last}"
        onboarding = (AS_OF - timedelta(days=int(onboarding_days))).date().isoformat()
        self.extra_customers.append({"customer_id": cid, "customer_name": name, "customer_type": "Individual",
                                     "occupation": name_hint, "expected_monthly_inflow_eur": float(self.rng.choice([800, 1500, 2500])),
                                     "onboarding_date": onboarding, "residence_country": "IE", "nationality": "IE",
                                     "risk_rating": "High" if status != "ACTIVE" else "Low", "declared_counterparty_countries": "",
                                     "in_alert_queue": False})
        idn = self.base_identity(cid, name, onboarding, account_status=status, status_reason=reason,
                                 prior_sar=prior_sar, status_typology=typology)
        return cid, idn

    # ---------------------------------------------------------------- scenarios
    def build(self):
        cust = self.customers.set_index("customer_id")
        cafe_users = []
        for r in self.truth.itertuples():
            c = cust.loc[r.customer_id]
            idn = self.base_identity(r.customer_id, c.customer_name, c.onboarding_date)
            if c.customer_type == "Individual" and self.rng.random() < 0.5:
                idn["devices"].append(self.device())
            if c.customer_type == "Individual":
                self.instrument(r.customer_id, "card")
            self.instrument(r.customer_id, "bank_account")
            fn = getattr(self, f"s_{r.scenario}", None)
            if fn:
                fn(r, idn, c)
            else:
                self.normal_logins(idn)
            if r.is_suspicious == 0:
                if self.rng.random() < 0.18:
                    cafe_users.append(r.customer_id)
                if c.customer_type == "Individual":
                    self._benign_noise(r, idn)
        # public wi-fi: many unrelated, ordinary customers share the same hotspot IPs (a weak link)
        for cid in cafe_users:
            idn = self.identity[cid]
            self.login(cid, self.ts(START, AS_OF), str(self.rng.choice(self._public_wifi)), "public_wifi",
                       ("Dublin", "IE", 53.35, -6.26), idn["devices"][0])

    def _benign_noise(self, r, idn):
        """Real-world signals that look bad and are not."""
        cid = r.customer_id
        city, lat, lon = idn["home_city"]
        if self.rng.random() < 0.10:      # privacy-conscious: uses a commercial VPN
            for _ in range(int(self.rng.integers(3, 7))):
                self.login(cid, self.ts(WINDOW_START, AS_OF), str(self.rng.choice(self._hosting)), "hosting_vpn",
                           HOSTING[int(self.rng.integers(0, 2))], idn["devices"][0])
        if self.rng.random() < 0.06:      # bought a second-hand phone once used by an account we later closed
            dev = self.device()
            ncid, nidn = self.network_account("Unemployed", "CLOSED", MULE_REASONS[0], True, "money_mule",
                                              int(self.rng.integers(300, 700)))
            for _ in range(4):
                self.login(ncid, self.ts(START, START + timedelta(days=12)), str(self.rng.choice(self._hosting)),
                           "hosting_vpn", HOSTING[0], dev)
            idn["devices"].append(dev)
            for _ in range(int(self.rng.integers(5, 9))):
                self.login(cid, self.ts(START + timedelta(days=40), AS_OF), idn["home_ip"], "residential", (city, "IE", lat, lon), dev)
        if self.rng.random() < 0.07:      # a payer disputed a payment, then withdrew it
            inbound = self._txns(cid, "IN", window=False)
            if len(inbound):
                t = inbound.sample(1, random_state=int(self.rng.integers(0, 1e6))).iloc[0]
                self.dispute(cid, t, "Unauthorised transaction", "Resolved: withdrawn by payer", "payer")

    def _txns(self, cid, direction=None, window=True, small=False):
        t = self.tx[self.tx.customer_id == cid]
        if window:
            t = t[pd.to_datetime(t.timestamp, format="ISO8601") >= WINDOW_START]
        if direction:
            t = t[t.direction == direction]
        if small:
            t = t[(t.channel == "SEPA") & (t.amount_eur < 2000)]
        return t

    def _ring(self, idn, hard, reasons, typology, n_range=(2, 5)):
        """Previously actioned accounts that share this customer's new device and/or withdrawal account."""
        new_dev = self.device()
        idn["devices"].append(new_dev)
        n = 1 if hard else int(self.rng.integers(*n_range))
        shared_fp = None if hard else f"BKF-{self._hex(12)}"
        for i in range(n):
            status = "CLOSED" if i == 0 else str(self.rng.choice(["CLOSED", "LIMITED", "ACTIVE"]))
            ncid, nidn = self.network_account("Unemployed", status, reasons[i % len(reasons)] if status != "ACTIVE" else "",
                                              prior_sar=(status == "CLOSED"), typology=typology if status != "ACTIVE" else "",
                                              onboarding_days=int(self.rng.integers(60, 400)))
            loc = CONTROLLER_LOCS[int(self.rng.integers(0, len(CONTROLLER_LOCS)))]
            shared = {"did": new_dev["did"], "vid": new_dev["vid"], "ua": new_dev["ua"]} if not hard else \
                {"did": f"DID-{self._hex()}", "vid": new_dev["vid"], "ua": new_dev["ua"]}   # hard: same browser cookie only
            for _ in range(int(self.rng.integers(3, 7))):
                self.login(ncid, self.ts(START, AS_OF), str(self.rng.choice(self._hosting)), "hosting_vpn", HOSTING[0], shared)
            self.login(ncid, self.ts(START, AS_OF), self.res_ip(), "residential", loc, shared)
            self.normal_logins(nidn, n=4, end=START + timedelta(days=30))
            self.instrument(ncid, "bank_account")
            if shared_fp:
                self.instrument(ncid, "bank_account", fingerprint=shared_fp, country="LT", label="LT** **** **** 4412")
        return new_dev, shared_fp

    def _mule_common(self, r, idn, c, hard):
        self.normal_logins(idn, end=WINDOW_START)                   # normal life before being recruited
        if hard and self.rng.random() < 0.6:                        # fresh recruit: no network, no complaints yet
            city, lat, lon = idn["home_city"]
            dev = self.device()
            idn["devices"].append(dev)
            for _ in range(int(self.rng.integers(5, 10))):
                self.login(r.customer_id, self.ts(WINDOW_START, AS_OF), idn["home_ip"], "residential", (city, "IE", lat, lon), dev)
            return
        new_dev, fp = self._ring(idn, hard, MULE_REASONS, "money_mule")
        if fp:
            self.instrument(r.customer_id, "bank_account", fingerprint=fp, country="LT", label="LT** **** **** 4412",
                            added=self.ts(WINDOW_START, WINDOW_START + timedelta(days=5)))
        loc = CONTROLLER_LOCS[int(self.rng.integers(0, len(CONTROLLER_LOCS)))]
        city, lat, lon = idn["home_city"]
        for _ in range(int(self.rng.integers(6, 12))):              # the new device takes over in the window
            x = self.rng.random()
            when = self.ts(WINDOW_START, AS_OF)
            if hard:
                self.login(r.customer_id, when, idn["home_ip"] if x < 0.6 else str(self.rng.choice(self._mobile)),
                           "residential" if x < 0.6 else "mobile", (city, "IE", lat, lon), new_dev)
            elif x < 0.5:
                self.login(r.customer_id, when, str(self.rng.choice(self._hosting)), "hosting_vpn", HOSTING[int(self.rng.integers(0, 2))], new_dev)
            elif x < 0.8:
                self.login(r.customer_id, when, self.res_ip(), "residential", loc, new_dev)
            else:
                self.login(r.customer_id, when, idn["home_ip"], "residential", (city, "IE", lat, lon), new_dev)
        # victims complain: payers dispute their payments as unauthorised / scam
        inbound = self._txns(r.customer_id, "IN", small=True)
        k = min(len(inbound), 1 if hard else int(self.rng.integers(2, 6)))
        for _, t in inbound.sample(k, random_state=int(self.rng.integers(0, 1e6))).iterrows():
            self.dispute(r.customer_id, t, str(self.rng.choice(["Unauthorised transaction", "Scam: purchase / investment (APP)"])),
                         str(self.rng.choice(["Open", "Open", "Lost by account holder"])), "payer")

    def s_money_mule(self, r, idn, c):
        self._mule_common(r, idn, c, r.variant == "hard")

    def s_mule_cash_out(self, r, idn, c):
        self._mule_common(r, idn, c, r.variant == "hard")

    def s_pass_through(self, r, idn, c):
        self.normal_logins(idn, end=WINDOW_START)
        hard = r.variant == "hard"
        loc = OFFSHORE_LOCS[int(self.rng.integers(0, len(OFFSHORE_LOCS)))]
        dev = self.device() if not hard else idn["devices"][0]
        if not hard:
            idn["devices"].append(dev)
            # phone number re-used from a previously closed account
            ncid, nidn = self.network_account("Director", "CLOSED", PT_REASONS[int(self.rng.integers(0, 2))], True, "pass_through",
                                              int(self.rng.integers(300, 900)))
            nidn["phone"] = idn["phone"]
            self.normal_logins(nidn, n=5, end=START + timedelta(days=20))
        for _ in range(int(self.rng.integers(5, 10))):
            if hard:
                self.login(r.customer_id, self.ts(WINDOW_START, AS_OF), str(self.rng.choice(self._hosting)), "hosting_vpn", HOSTING[1], dev)
            else:
                self.login(r.customer_id, self.ts(WINDOW_START, AS_OF), self.res_ip(), "residential", loc, dev)

    def s_sanctions_exposure(self, r, idn, c):
        self.normal_logins(idn)
        if r.variant != "hard":
            for _ in range(2):
                self.login(r.customer_id, self.ts(WINDOW_START, AS_OF), self.res_ip(), "residential",
                           SANCTIONED_LOCS[int(self.rng.integers(0, 2))], idn["devices"][0])

    def s_sanctions_transliteration(self, r, idn, c):
        self.normal_logins(idn)
        n = 1 if r.variant == "hard" else int(self.rng.integers(2, 4))
        for _ in range(n):
            self.login(r.customer_id, self.ts(WINDOW_START, AS_OF), self.res_ip(), "residential",
                       SANCTIONED_LOCS[int(self.rng.integers(0, 2))], idn["devices"][0])

    # benign scenarios -----------------------------------------------------------------------------------------
    def s_shared_expenses(self, r, idn, c):
        """Flatmates share the home IP. They are also among the people paying 'rent share'."""
        self.normal_logins(idn)
        payers = self._txns(r.customer_id, "IN").counterparty_name.unique().tolist()
        for name in payers[: int(self.rng.integers(2, 4))]:
            self.extra_customers.append({"customer_id": (ncid := self._id("CUS")), "customer_name": name, "customer_type": "Individual",
                                         "occupation": "Student", "expected_monthly_inflow_eur": 900.0,
                                         "onboarding_date": (AS_OF - timedelta(days=int(self.rng.integers(200, 900)))).date().isoformat(),
                                         "residence_country": "IE", "nationality": "IE", "risk_rating": "Low",
                                         "declared_counterparty_countries": "", "in_alert_queue": False})
            nidn = self.base_identity(ncid, name, self.extra_customers[-1]["onboarding_date"], home_ip=idn["home_ip"], home_city=idn["home_city"])
            self.normal_logins(nidn, n=8)
            self.instrument(ncid, "card")

    def s_cash_intensive_business(self, r, idn, c):
        self.normal_logins(idn)
        for _ in range(int(self.rng.integers(1, 3))):                # staff log in to their own accounts from the office wi-fi
            ncid, nidn = self.network_account("Restaurant staff", "ACTIVE", "", False, "", int(self.rng.integers(200, 1500)))
            nidn["home_ip"] = idn["home_ip"]
            self.normal_logins(nidn, n=6)

    def s_online_retailer(self, r, idn, c):
        self.normal_logins(idn)
        orders = self._txns(r.customer_id, "IN", window=False)
        orders = orders[orders.reference == "Order payment"]
        k = int(self.rng.integers(4, 7)) if r.variant == "hard" else int(self.rng.integers(1, 3))
        for _, t in orders.sample(min(k, len(orders)), random_state=int(self.rng.integers(0, 1e6))).iterrows():
            self.dispute(r.customer_id, t, str(self.rng.choice(["Item not received", "Item not as described"])),
                         str(self.rng.choice(["Resolved: seller won", "Resolved: refunded", "Resolved: seller won"])), "buyer")

    def s_new_cash_business(self, r, idn, c):
        self.normal_logins(idn, start=pd.Timestamp(c.onboarding_date).to_pydatetime())

    def s_own_account_sweep(self, r, idn, c):
        self.normal_logins(idn)
        self.instrument(r.customer_id, "bank_account", label="IE** **** **** own investment account")

    # ---------------------------------------------------------------- output
    def frames(self) -> dict[str, pd.DataFrame]:
        cust = pd.concat([self.customers.assign(in_alert_queue=True), pd.DataFrame(self.extra_customers)], ignore_index=True)
        ident = pd.DataFrame([{k: v for k, v in i.items() if k not in ("devices", "home_city")} |
                              {"home_city": i["home_city"][0]} for i in self.identity.values()])
        cust = cust.merge(ident.drop(columns=["home_ip"]), on="customer_id", how="left")
        cust = cust.rename(columns={"signup_device": "signup_device_id"})
        return {
            "customers": cust,
            "logins": pd.DataFrame(self.logins).sort_values(["customer_id", "timestamp"]).reset_index(drop=True),
            "instruments": pd.DataFrame(self.instruments),
            "disputes": pd.DataFrame(self.disputes, columns=["dispute_id", "customer_id", "txn_id", "opened", "reason", "amount_eur",
                                                              "status", "filed_by", "counterparty_name"]),
        }


def add_identity_layer(frames: dict[str, pd.DataFrame], seed: int) -> dict[str, pd.DataFrame]:
    b = IdentityBuilder(frames, seed)
    b.build()
    return frames | b.frames()
