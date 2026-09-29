"""Central configuration: thresholds, reference lists and policy settings.

Everything a compliance officer might want to tune or audit lives here, so the
agent's behaviour can be reviewed in one place rather than hunted for in code.
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
REPORTS_DIR = ROOT / "reports"
AUDIT_DIR = ROOT / "audit"

AGENT_VERSION = "0.1.0"

# --- Reference data (illustrative only) -------------------------------------
# FATF "call for action" jurisdictions at the time of writing. Illustrative:
# a production system would load this from a maintained reference feed.
HIGH_RISK_COUNTRIES = {"IR": "Iran", "KP": "North Korea", "MM": "Myanmar"}

CRYPTO_EXCHANGE_KEYWORDS = ("exchange", "crypto", "coin", "bitpay", "wallet")

# --- Detection thresholds ----------------------------------------------------
CASH_REPORTING_THRESHOLD = 10_000.0     # EUR
NEAR_THRESHOLD_FLOOR = 7_000.0          # cash deposits in [floor, threshold) are "near threshold"
LOOKBACK_DAYS = 30                      # alert review window
HISTORY_DAYS = 60                       # prior behaviour used as the customer's own baseline
PASS_THROUGH_WINDOW_HOURS = 72          # outbound within this window of a large credit counts as pass-through
LARGE_CREDIT_MIN = 5_000.0

# --- Sanctions screening ------------------------------------------------------
SANCTIONS_MATCH_THRESHOLD = 88          # fuzzy score (0-100) treated as a potential match

# --- Guardrail policy ---------------------------------------------------------
# A CLOSE recommendation below this confidence is never auto-closed: it goes to a human.
AUTO_CLOSE_MIN_CONFIDENCE = float(os.getenv("AML_AUTO_CLOSE_MIN_CONFIDENCE", "0.85"))

# --- LLM agent ------------------------------------------------------------------
LLM_MODEL = os.getenv("AML_AGENT_MODEL", "claude-sonnet-5-5")
LLM_MAX_TURNS = 12

# --- Illustrative workload assumptions (used only for the ROI estimate in reports)
MANUAL_MINUTES_PER_ALERT = 45
REVIEW_MINUTES_PER_AGENT_CASE = 12
QA_SAMPLE_MINUTES_PER_AUTO_CLOSE = 3
