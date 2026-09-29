"""Synthetic AML dataset generator with ground-truth labels.

Produces a realistic-looking alert queue for a payments / e-money firm:

* customers.csv      KYC profile per customer
* transactions.csv   ~90 days of account activity per alerted customer
* alerts.csv         one transaction-monitoring alert per customer (what an analyst sees)
* ground_truth.csv   the label and typology for each alert (hidden from the agent)
* sanctions_list.csv a small, entirely fictional sanctions list
* logins.csv, instruments.csv, disputes.csv and extra network accounts: see identity_gen.py (v2)

Five suspicious typologies and five benign "false positive" scenarios are
generated. Each has a standard and a harder variant, so an evaluation shows
where an agent breaks rather than just a headline score. All names and
entities are fictional.

Run:  python -m amlagent.data_gen
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from .config import DATA_DIR, HIGH_RISK_COUNTRIES

AS_OF = datetime(2026, 9, 1, 9, 0)          # alert generation date
HISTORY_START = AS_OF - timedelta(days=90)
WINDOW_START = AS_OF - timedelta(days=30)

FIRST = ["Aoife", "Liam", "Sofia", "Mateo", "Hannah", "Lukas", "Chloe", "Noah", "Mia", "Jonas",
         "Priya", "Arjun", "Fatima", "Omar", "Elena", "Marco", "Yuki", "Kenji", "Grace", "Daniel",
         "Zara", "Ibrahim", "Lena", "Felix", "Nora", "Tomas", "Ines", "Rafael", "Clara", "Emil",
         "Ana", "Kofi", "Amara", "Chen", "Mei", "Oisin", "Ciara", "Pavel", "Olga", "Samir"]
LAST = ["Murphy", "Kelly", "Schmidt", "Garcia", "Rossi", "Novak", "Dubois", "Nowak", "Silva",
        "Byrne", "Walsh", "Fischer", "Weber", "Costa", "Moreau", "Jansen", "Kowalski", "Horvat",
        "Lindqvist", "Okafor", "Mensah", "Tanaka", "Sato", "Kumar", "Sharma", "Haddad", "Nasser",
        "Popescu", "Ivanova", "OBrien", "Doyle", "Keller", "Brennan", "Santos", "Lopez", "Meyer"]
EU = ["IE", "DE", "FR", "NL", "ES", "IT", "PT", "BE", "AT", "PL"]

# Entirely fictional sanctioned parties. Any resemblance to real entities is coincidental.
SANCTIONS = [
    ("SL-001", "Volkov Maritime Trading LLC", "entity", "RU", "SYNTH-RUS"),
    ("SL-002", "Kasparian Petrochem Holdings", "entity", "IR", "SYNTH-IRN"),
    ("SL-003", "Grenhold Shipping Company", "entity", "KP", "SYNTH-DPRK"),
    ("SL-004", "Talvar Defence Supplies Ltd", "entity", "IR", "SYNTH-IRN"),
    ("SL-005", "Boris Anatolyevich Kren", "individual", "RU", "SYNTH-RUS"),
    ("SL-006", "Northwind Metals Export JSC", "entity", "BY", "SYNTH-BLR"),
    ("SL-007", "Zafar Global Logistics FZE", "entity", "SY", "SYNTH-SYR"),
    ("SL-008", "Irina Mstislavovna Dolgova", "individual", "RU", "SYNTH-RUS"),
    ("SL-009", "Crimson Delta Mining Corp", "entity", "MM", "SYNTH-MMR"),
    ("SL-010", "Petrov Logistics LLC", "entity", "RU", "SYNTH-RUS"),
    ("SL-011", "Orinoco Gold Resources CA", "entity", "VE", "SYNTH-VEN"),
    ("SL-012", "Haedong Precision Machinery", "entity", "KP", "SYNTH-DPRK"),
    ("SL-013", "Arash Kiani Mehrabad", "individual", "IR", "SYNTH-IRN"),
    ("SL-014", "Steppe Energy Finance Group", "entity", "RU", "SYNTH-RUS"),
    ("SL-015", "Minsk Optic Components OAO", "entity", "BY", "SYNTH-BLR"),
]
# (true-hit spelling variant, same country) for each sanctioned party we use in scenarios
TRUE_HIT_VARIANTS = [
    ("Volkov Maritme Trading", "RU"), ("Kasparian Petro-Chem Holdings", "IR"),
    ("Grenhold Shipping Co", "KP"), ("Talvar Defense Supplies", "IR"),
    ("Boris A. Kren", "RU"), ("North Wind Metals Export", "BY"),
    ("Zafar Global Logistic FZE", "SY"), ("Irina M. Dolgova", "RU"),
    ("Crimson Delta Mining Corporation", "MM"), ("Steppe Energy Finance Grp", "RU"),
    ("Orinoco Gold Resources", "VE"), ("Haedong Precision Machine", "KP"),
]
# names that look like a sanctioned party but are a different, legitimate business
FALSE_HIT_LOOKALIKES = [
    ("Petrov Logistics GmbH", "DE"), ("Volkov Maritime Services BV", "NL"),
    ("Northwind Metals Ltd", "IE"), ("Crimson Delta Consulting", "GB"),
    ("Steppe Energy Advisors SARL", "FR"), ("Zafar Global Logistics Ltd", "GB"),
    ("Orinoco Gold Jewellers", "ES"), ("Minsk Optics Service", "PL"),
]

MERCHANTS = ["Tesco", "Lidl", "Aldi", "Spar", "Boots", "Zara", "Amazon EU", "Uber", "Deliveroo",
             "Ryanair", "Circle K", "Netflix", "Spotify", "Penneys", "Dunnes Stores", "Apple"]
BRANCHES = ["Main St branch", "Airport branch", "City Centre branch", "Retail Park branch",
            "Northside branch", "Southside branch"]
CRYPTO_EXCHANGES = [("CoinVault Exchange", "LT"), ("BitHarbor Crypto", "EE"), ("Nordic Coin Exchange", "SE")]


@dataclass
class Builder:
    rng: np.random.Generator
    customers: list = field(default_factory=list)
    txns: list = field(default_factory=list)
    alerts: list = field(default_factory=list)
    truth: list = field(default_factory=list)
    _txn_seq: int = 100000

    # -- helpers ---------------------------------------------------------------
    def name(self) -> str:
        return f"{self.rng.choice(FIRST)} {self.rng.choice(LAST)}"

    def ts(self, start: datetime, end: datetime) -> datetime:
        span = (end - start).total_seconds()
        t = start + timedelta(seconds=float(self.rng.uniform(0, span)))
        return t.replace(microsecond=0)

    def add_txn(self, cid, when, direction, amount, channel, cp_name, cp_country, ref=""):
        self._txn_seq += 1
        self.txns.append({
            "txn_id": f"TXN-{self._txn_seq}", "customer_id": cid,
            "timestamp": when.replace(microsecond=0).isoformat(sep=" "), "direction": direction,
            "amount_eur": round(float(amount), 2), "channel": channel,
            "counterparty_name": cp_name, "counterparty_country": cp_country, "reference": ref,
        })

    def add_customer(self, cid, **kw):
        base = {"customer_id": cid, "onboarding_date": (AS_OF - timedelta(days=int(self.rng.integers(200, 2500)))).date().isoformat(),
                "residence_country": "IE", "nationality": str(self.rng.choice(EU)), "risk_rating": "Low",
                "declared_counterparty_countries": ""}
        base.update(kw)
        self.customers.append(base)
        return base

    def add_alert(self, cid, rule, desc, label, typology, scenario, variant):
        aid = f"ALR-{len(self.alerts) + 1:04d}"
        self.alerts.append({"alert_id": aid, "customer_id": cid, "alert_date": AS_OF.date().isoformat(),
                            "rule_id": rule[0], "rule_name": rule[1], "rule_description": desc,
                            "lookback_days": 30})
        self.truth.append({"alert_id": aid, "is_suspicious": label, "typology": typology,
                           "scenario": scenario, "variant": variant})

    # -- baseline activity ---------------------------------------------------------
    def individual_baseline(self, cid, cust_name, monthly_income, employer):
        for m in range(3):
            pay = AS_OF - timedelta(days=5 + 30 * m) + timedelta(hours=int(self.rng.integers(0, 6)))
            self.add_txn(cid, pay, "IN", monthly_income * self.rng.uniform(0.98, 1.02), "SEPA", employer, "IE", "SALARY")
            self.add_txn(cid, pay + timedelta(days=2), "OUT", monthly_income * self.rng.uniform(0.3, 0.4), "SEPA",
                         f"{self.rng.choice(LAST)} Property Mgmt", "IE", "RENT")
        for _ in range(int(self.rng.integers(25, 45))):
            self.add_txn(cid, self.ts(HISTORY_START, AS_OF), "OUT", self.rng.uniform(4, 140), "CARD",
                         str(self.rng.choice(MERCHANTS)), "IE", "")

    def business_baseline(self, cid, monthly_turnover, n_payers=25):
        payers = [f"{self.rng.choice(LAST)} {self.rng.choice(['Ltd', 'Services', 'Group', 'Retail'])}" for _ in range(n_payers)]
        for _ in range(int(self.rng.integers(18, 30))):
            self.add_txn(cid, self.ts(HISTORY_START, AS_OF), "IN", self.rng.uniform(0.02, 0.08) * monthly_turnover,
                         "SEPA", str(self.rng.choice(payers)), "IE", "Invoice")
        for m in range(3):
            self.add_txn(cid, AS_OF - timedelta(days=10 + 30 * m), "OUT", monthly_turnover * self.rng.uniform(0.3, 0.45),
                         "SEPA", "Payroll Bureau Ltd", "IE", "PAYROLL")
            self.add_txn(cid, AS_OF - timedelta(days=15 + 30 * m), "OUT", monthly_turnover * self.rng.uniform(0.2, 0.3),
                         "SEPA", f"{self.rng.choice(LAST)} Wholesale", "IE", "Supplier")

    def new_individual(self, cid, occupation, income, **kw):
        nm = self.name()
        self.add_customer(cid, customer_name=nm, customer_type="Individual", occupation=occupation,
                          expected_monthly_inflow_eur=income, **kw)
        self.individual_baseline(cid, nm, income, f"{self.rng.choice(LAST)} {self.rng.choice(['Ltd', 'Group', 'Hospital', 'Stores'])}")
        return nm

    def new_business(self, cid, activity, turnover, n_payers=25, **kw):
        nm = f"{self.rng.choice(LAST)} {activity.split()[0]} Ltd"
        self.add_customer(cid, customer_name=nm, customer_type="Business", occupation=activity,
                          expected_monthly_inflow_eur=turnover, **kw)
        self.business_baseline(cid, turnover, n_payers)
        return nm

    # -- suspicious scenarios ---------------------------------------------------------
    def structuring(self, cid, hard):
        income = float(self.rng.choice([1900, 2200, 2600, 3000]))
        self.new_individual(cid, str(self.rng.choice(["Retail assistant", "Warehouse operative", "Barista"])), income)
        if hard:   # fewer, more spread out, some well below the near-threshold band
            n, lo, hi, days = int(self.rng.integers(3, 5)), 5_500, 9_600, 28
        else:
            n, lo, hi, days = int(self.rng.integers(4, 8)), 8_600, 9_950, 16
        start = AS_OF - timedelta(days=days)
        for _ in range(n):
            self.add_txn(cid, self.ts(start, AS_OF), "IN", self.rng.uniform(lo, hi), "CASH_DEPOSIT",
                         str(self.rng.choice(BRANCHES)), "IE", "Cash lodgement")
        # then moves most of it out
        self.add_txn(cid, AS_OF - timedelta(days=1), "OUT", n * (lo + hi) / 2 * 0.7, "SEPA",
                     self.name(), str(self.rng.choice(EU)), "Loan repayment")
        self.add_alert(cid, ("TM-CASH-01", "Cash deposits near reporting threshold"),
                       "Multiple cash deposits within 30 days, aggregate exceeds expected activity",
                       1, "structuring", "structuring", "hard" if hard else "standard")

    def pass_through(self, cid, hard):
        if hard:
            self.new_business(cid, "Consulting services", float(self.rng.choice([15000, 20000])))
            n, lag_h, share, ref_in = int(self.rng.integers(3, 5)), (60, 110), (0.80, 0.90), "Invoice consultancy"
        else:
            self.new_individual(cid, str(self.rng.choice(["Student", "Unemployed", "Delivery driver"])),
                                float(self.rng.choice([900, 1400, 1800])))
            n, lag_h, share, ref_in = int(self.rng.integers(3, 6)), (2, 40), (0.92, 0.99), "Payment"
        for _ in range(n):
            t_in = self.ts(WINDOW_START, AS_OF - timedelta(days=5))
            amt = self.rng.uniform(15_000, 60_000)
            self.add_txn(cid, t_in, "IN", amt, "WIRE", f"{self.rng.choice(LAST)} Trading Ltd",
                         str(self.rng.choice(["CY", "LV", "AE", "HK"])), ref_in)
            remaining = amt * self.rng.uniform(*share)
            k = int(self.rng.integers(1, 3))       # split onward payment across 1-2 beneficiaries
            for _ in range(k):
                self.add_txn(cid, t_in + timedelta(hours=float(self.rng.uniform(*lag_h))), "OUT", remaining / k,
                             "WIRE", f"{self.rng.choice(LAST)} {self.rng.choice(['Holdings', 'Import', 'Capital'])}",
                             str(self.rng.choice(["HK", "AE", "TR", "GE", "CY"])), "Services")
        self.add_alert(cid, ("TM-RMF-02", "Rapid movement of funds"),
                       "Large credits followed by outgoing transfers of similar value",
                       1, "pass_through", "pass_through", "hard" if hard else "standard")

    def mule(self, cid, hard):
        income = float(self.rng.choice([600, 800, 1100]))
        self.new_individual(cid, str(self.rng.choice(["Student", "Part-time retail", "Unemployed"])), income)
        n = int(self.rng.integers(8, 11)) if hard else int(self.rng.integers(15, 31))
        total = 0.0
        for _ in range(n):
            amt = self.rng.uniform(150, 900)
            total += amt
            self.add_txn(cid, self.ts(WINDOW_START, AS_OF - timedelta(days=2)), "IN", amt, "SEPA",
                         self.name(), str(self.rng.choice(EU)), str(self.rng.choice(["rent", "thanks", "for item", "loan", ""])))
        ex, ex_c = CRYPTO_EXCHANGES[int(self.rng.integers(0, len(CRYPTO_EXCHANGES)))]
        k = int(self.rng.integers(2, 5))
        for _ in range(k):
            self.add_txn(cid, self.ts(WINDOW_START + timedelta(days=3), AS_OF), "OUT", total * 0.88 / k, "SEPA",
                         ex, ex_c, "Top up")
        self.add_alert(cid, ("TM-FAN-03", "High number of unique inbound counterparties"),
                       "Unusual number of distinct payers relative to customer profile",
                       1, "money_mule", "money_mule", "hard" if hard else "standard")

    def high_risk_jurisdiction(self, cid, hard):
        self.new_individual(cid, str(self.rng.choice(["Software engineer", "Nurse", "Sales manager"])),
                            float(self.rng.choice([3500, 4200, 5000])))
        n = 1 if hard else int(self.rng.integers(2, 5))
        codes = list(HIGH_RISK_COUNTRIES)
        for _ in range(n):
            self.add_txn(cid, self.ts(WINDOW_START, AS_OF), "OUT", self.rng.uniform(4_000, 20_000), "WIRE",
                         f"{self.rng.choice(['Pars', 'Golden', 'Eastern', 'Lotus'])} {self.rng.choice(['Trading', 'Exchange', 'Services'])} Co",
                         str(self.rng.choice(codes)), str(self.rng.choice(["gift", "services", "consulting", "family"])))
        self.add_alert(cid, ("TM-HRJ-04", "Transaction with high-risk jurisdiction"),
                       "Outbound payment to a FATF call-for-action jurisdiction",
                       1, "high_risk_jurisdiction", "high_risk_jurisdiction", "hard" if hard else "standard")

    def sanctions(self, cid, hard, idx):
        if self.rng.random() < 0.5:
            self.new_individual(cid, "Shipping broker", 6000.0)
        else:
            self.new_business(cid, "Freight forwarding", 45000.0)
        cp, cc = TRUE_HIT_VARIANTS[idx % len(TRUE_HIT_VARIANTS)]
        if hard:   # indirect: payment routed via an intermediary country, variant spelling
            self.add_txn(cid, self.ts(WINDOW_START, AS_OF), "OUT", self.rng.uniform(8_000, 35_000), "WIRE", cp, cc,
                         "Freight charges")
        else:
            for _ in range(2):
                self.add_txn(cid, self.ts(WINDOW_START, AS_OF), str(self.rng.choice(["IN", "OUT"])),
                             self.rng.uniform(10_000, 80_000), "WIRE", cp, cc, "Contract payment")
        self.add_alert(cid, ("SCR-PAY-05", "Payment screening potential match"),
                       "Counterparty name matched against sanctions list",
                       1, "sanctions_exposure", "sanctions_exposure", "hard" if hard else "standard")

    # -- benign scenarios -------------------------------------------------------------
    def cash_business(self, cid, hard):
        turnover = float(self.rng.choice([35000, 45000, 60000]))
        self.new_business(cid, str(self.rng.choice(["Restaurant", "Convenience store", "Takeaway food"])), turnover)
        # consistent cash lodgements across the whole history, not just the alert window
        hi = 9_900 if hard else 8_500
        for _ in range(int(self.rng.integers(24, 36))):
            self.add_txn(cid, self.ts(HISTORY_START, AS_OF), "IN", self.rng.uniform(2_500, hi), "CASH_DEPOSIT",
                         str(self.rng.choice(BRANCHES[:2])), "IE", "Takings")
        self.add_alert(cid, ("TM-CASH-01", "Cash deposits near reporting threshold"),
                       "Multiple cash deposits within 30 days, aggregate exceeds expected activity",
                       0, "none", "cash_intensive_business", "hard" if hard else "standard")

    def one_off_large_credit(self, cid, hard):
        nm = self.new_individual(cid, str(self.rng.choice(["Teacher", "Civil servant", "Retired"])),
                                 float(self.rng.choice([2800, 3400, 4100])))
        amt = self.rng.uniform(150_000, 420_000)
        t = self.ts(WINDOW_START, AS_OF - timedelta(days=10))
        self.add_txn(cid, t, "IN", amt, "SEPA", f"{self.rng.choice(LAST)} & Co Solicitors", "IE",
                     str(self.rng.choice(["Property sale completion", "Estate distribution", "Sale proceeds"])))
        if hard:   # part moves on quickly, but to the customer's own account
            self.add_txn(cid, t + timedelta(hours=30), "OUT", amt * 0.6, "SEPA", f"{nm} (own savings account)", "IE",
                         "Transfer to own account")
        self.add_alert(cid, ("TM-LGC-06", "Large credit inconsistent with profile"),
                       "Single credit significantly above expected monthly inflow",
                       0, "none", "one_off_large_credit", "hard" if hard else "standard")

    def own_account_sweep(self, cid, hard):
        income = float(self.rng.choice([5200, 6500, 8000]))
        nm = self.new_individual(cid, str(self.rng.choice(["Solicitor", "Consultant", "Engineer"])), income)
        for m in range(3 if hard else 1):
            t = AS_OF - timedelta(days=5 + 30 * m) + timedelta(hours=8)
            self.add_txn(cid, t, "IN", self.rng.uniform(15_000, 30_000), "SEPA", "Revenue Commissioners Refund" if m == 0 and hard else f"{self.rng.choice(LAST)} Bonus Payroll", "IE", "Bonus")
            self.add_txn(cid, t + timedelta(hours=float(self.rng.uniform(3, 20))), "OUT", self.rng.uniform(14_000, 15_000),
                         "SEPA", f"{nm} (own investment account)", "IE", "Transfer to own account")
        self.add_alert(cid, ("TM-RMF-02", "Rapid movement of funds"),
                       "Large credits followed by outgoing transfers of similar value",
                       0, "none", "own_account_sweep", "hard" if hard else "standard")

    def sanctions_false_hit(self, cid, hard, idx):
        self.new_business(cid, str(self.rng.choice(["Freight forwarding", "Auto parts import", "Engineering services"])), 55000.0)
        cp, cc = FALSE_HIT_LOOKALIKES[idx % len(FALSE_HIT_LOOKALIKES)]
        for _ in range(int(self.rng.integers(1, 3))):
            self.add_txn(cid, self.ts(WINDOW_START, AS_OF), "OUT", self.rng.uniform(3_000, 25_000), "SEPA", cp, cc, "Invoice")
        if hard:   # longstanding relationship: same payee throughout history
            for _ in range(4):
                self.add_txn(cid, self.ts(HISTORY_START, WINDOW_START), "OUT", self.rng.uniform(3_000, 25_000), "SEPA", cp, cc, "Invoice")
        self.add_alert(cid, ("SCR-PAY-05", "Payment screening potential match"),
                       "Counterparty name matched against sanctions list",
                       0, "none", "sanctions_false_hit", "hard" if hard else "standard")

    def online_retailer(self, cid, hard):
        turnover = float(self.rng.choice([20000, 30000, 40000]))
        self.new_business(cid, "Online retail (e-commerce)", turnover, n_payers=5)
        # many small customer payments across the whole history, consistent with the business model
        n_hist = int(self.rng.integers(40, 70))
        for _ in range(n_hist):
            self.add_txn(cid, self.ts(HISTORY_START, AS_OF), "IN", self.rng.uniform(20, 250), "SEPA",
                         self.name(), str(self.rng.choice(EU)), "Order payment")
        if hard:   # a seasonal spike in the alert window plus a payout to a crypto-sounding payment processor
            for _ in range(25):
                self.add_txn(cid, self.ts(WINDOW_START, AS_OF), "IN", self.rng.uniform(20, 250), "SEPA",
                             self.name(), str(self.rng.choice(EU)), "Order payment")
        self.add_alert(cid, ("TM-FAN-03", "High number of unique inbound counterparties"),
                       "Unusual number of distinct payers relative to customer profile",
                       0, "none", "online_retailer", "hard" if hard else "standard")

    # -- ambiguous scenarios: where fixed rules struggle and context has to be read ---------------
    def mule_cash_out(self, cid, hard):
        """Suspicious. Fan-in from many payers, but cashed out to individuals abroad instead of crypto,
        on a customer whose occupation does not look 'low income'."""
        self.new_individual(cid, str(self.rng.choice(["Graphic designer", "Freelance photographer", "Personal trainer"])),
                            float(self.rng.choice([1800, 2300])))
        n = int(self.rng.integers(9, 13)) if hard else int(self.rng.integers(12, 22))
        total = 0.0
        for _ in range(n):
            amt = self.rng.uniform(300, 1500)
            total += amt
            self.add_txn(cid, self.ts(WINDOW_START, AS_OF - timedelta(days=3)), "IN", amt, "SEPA", self.name(),
                         str(self.rng.choice(EU)), str(self.rng.choice(["invoice", "design work", "deposit", "payment"])))
        for _ in range(int(self.rng.integers(2, 4))):
            self.add_txn(cid, self.ts(WINDOW_START + timedelta(days=5), AS_OF), "OUT", total * 0.3, "SEPA",
                         self.name(), str(self.rng.choice(["RO", "BG", "LT", "PT"])), "")
        self.add_alert(cid, ("TM-FAN-03", "High number of unique inbound counterparties"),
                       "Unusual number of distinct payers relative to customer profile",
                       1, "money_mule", "mule_cash_out", "hard" if hard else "standard")

    def sanctions_transliteration(self, cid, hard, idx):
        """Suspicious. A sanctioned party paid under a transliterated/abbreviated name that scores below
        the fuzzy threshold. The screening tool surfaces it only as a near-miss."""
        self.new_business(cid, str(self.rng.choice(["Metals trading", "Industrial equipment import"])), 70000.0)
        variants = [("Volkoff Marine Trade LLC", "RU"), ("Severny Veter Metals Export", "BY"),
                    ("Steppe Enrg Fin Grp", "RU"), ("Orinoko Gold Res.", "VE"), ("Zafar Glb Logistics", "SY"),
                    ("Minskaya Optika Components", "BY")]
        cp, cc = variants[idx % len(variants)]
        self.add_txn(cid, self.ts(WINDOW_START, AS_OF), "OUT", self.rng.uniform(20_000, 90_000), "WIRE", cp, cc,
                     "Advance payment contract 2026/" + str(int(self.rng.integers(10, 99))))
        self.add_alert(cid, ("TM-HVW-07", "High-value wire to new beneficiary"),
                       "First payment to a new beneficiary above EUR 20,000",
                       1, "sanctions_exposure", "sanctions_transliteration", "hard" if hard else "standard")

    def shared_expenses(self, cid, hard):
        """Benign. A student collecting shares of rent, a holiday and event tickets from friends."""
        nm = self.new_individual(cid, "Student", float(self.rng.choice([700, 900])))
        friends = [self.name() for _ in range(int(self.rng.integers(10, 16)))]
        refs = ["rent share", "holiday Lisbon", "concert tix", "flat bills", "Airbnb split", "bday dinner"]
        total = 0.0
        for f in friends:
            amt = self.rng.uniform(40, 450)
            total += amt
            self.add_txn(cid, self.ts(WINDOW_START, AS_OF - timedelta(days=4)), "IN", amt, "SEPA", f, "IE", str(self.rng.choice(refs)))
        self.add_txn(cid, AS_OF - timedelta(days=3), "OUT", total * 0.55, "SEPA", f"{self.rng.choice(LAST)} Lettings", "IE", "Rent Oct flat 4B")
        self.add_txn(cid, AS_OF - timedelta(days=2), "OUT", total * 0.35, "CARD", "Airbnb", "IE", "Lisbon booking")
        self.add_alert(cid, ("TM-FAN-03", "High number of unique inbound counterparties"),
                       "Unusual number of distinct payers relative to customer profile",
                       0, "none", "shared_expenses", "hard" if hard else "standard")

    def new_cash_business(self, cid, hard):
        """Benign. A recently onboarded restaurant: no history to compare with, but deposits match
        the declared turnover and business type."""
        turnover = float(self.rng.choice([40000, 55000]))
        nm = f"{self.rng.choice(LAST)} Restaurant Ltd"
        self.add_customer(cid, customer_name=nm, customer_type="Business", occupation="Restaurant",
                          expected_monthly_inflow_eur=turnover,
                          onboarding_date=(AS_OF - timedelta(days=int(self.rng.integers(32, 45)))).date().isoformat(),
                          risk_rating="Medium")
        n = int(self.rng.integers(8, 13))
        for _ in range(n):
            self.add_txn(cid, self.ts(WINDOW_START, AS_OF), "IN", turnover / n * self.rng.uniform(0.7, 1.1),
                         "CASH_DEPOSIT", str(self.rng.choice(BRANCHES[:2])), "IE", "Takings")
        for _ in range(int(self.rng.integers(5, 9))):
            self.add_txn(cid, self.ts(WINDOW_START, AS_OF), "OUT", self.rng.uniform(1500, 6000), "SEPA",
                         f"{self.rng.choice(LAST)} Food Wholesale", "IE", "Supplier")
        self.add_alert(cid, ("TM-CASH-01", "Cash deposits near reporting threshold"),
                       "Multiple cash deposits within 30 days, aggregate exceeds expected activity",
                       0, "none", "new_cash_business", "hard" if hard else "standard")


def generate(seed: int = 42, per_suspicious: int = 16, per_benign: int = 24, per_ambiguous: int = 12,
             with_identity: bool = True) -> dict[str, pd.DataFrame]:
    b = Builder(rng=np.random.default_rng(seed))
    cid_seq = iter(range(10001, 99999))
    suspicious = [b.structuring, b.pass_through, b.mule, b.high_risk_jurisdiction, b.sanctions]
    benign = [b.cash_business, b.one_off_large_credit, b.own_account_sweep, b.sanctions_false_hit, b.online_retailer]
    ambiguous = [b.mule_cash_out, b.sanctions_transliteration, b.shared_expenses, b.new_cash_business]
    jobs = []
    for fn in suspicious:
        jobs += [(fn, i, i % 3 == 2) for i in range(per_suspicious)]      # ~1/3 hard
    for fn in benign:
        jobs += [(fn, i, i % 3 == 2) for i in range(per_benign)]
    for fn in ambiguous:
        jobs += [(fn, i, i % 3 == 2) for i in range(per_ambiguous)]
    order = b.rng.permutation(len(jobs))                                 # shuffle the queue
    for k in order:
        fn, i, hard = jobs[k]
        cid = f"CUS-{next(cid_seq)}"
        if fn in (b.sanctions, b.sanctions_false_hit, b.sanctions_transliteration):
            fn(cid, hard, i)
        else:
            fn(cid, hard)
    txns = pd.DataFrame(b.txns).sort_values(["customer_id", "timestamp"]).reset_index(drop=True)
    frames = {
        "customers": pd.DataFrame(b.customers),
        "transactions": txns,
        "alerts": pd.DataFrame(b.alerts),
        "ground_truth": pd.DataFrame(b.truth),
        "sanctions_list": pd.DataFrame(SANCTIONS, columns=["list_id", "name", "party_type", "country", "programme"]),
    }
    if not with_identity:
        return frames
    from .identity_gen import add_identity_layer   # v2: identity, logins, devices, instruments, disputes, network accounts
    return add_identity_layer(frames, seed)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    frames = generate(args.seed)
    for name, df in frames.items():
        df.to_csv(DATA_DIR / f"{name}.csv", index=False)
    gt = frames["ground_truth"]
    print(f"Wrote {len(frames['alerts'])} alerts, {len(frames['customers'])} customers "
          f"({int(frames['customers'].in_alert_queue.sum())} alerted + network accounts), {len(frames['transactions'])} transactions, "
          f"{len(frames['logins'])} logins, {len(frames['instruments'])} instruments, {len(frames['disputes'])} disputes to {DATA_DIR}")
    print(gt.groupby(["is_suspicious", "scenario"]).size().to_string())


if __name__ == "__main__":
    main()
