"""The decision contract every agent must return."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

DISPOSITIONS = ("CLOSE_FALSE_POSITIVE", "ESCALATE_L2", "RECOMMEND_SAR")
ACCOUNT_ACTIONS = ("NO_ACTION", "REQUEST_DOCUMENTS", "RESTRICT", "CLOSE_ACCOUNT")
TYPOLOGIES = ("structuring", "pass_through", "money_mule", "high_risk_jurisdiction", "sanctions_exposure", "none")

# Final workflow status after guardrails. Note: nothing is ever filed or closed without a route to a human.
AUTO_CLOSE_ELIGIBLE = "AUTO_CLOSE_ELIGIBLE"        # closed, subject to sampled QA
HUMAN_REVIEW = "HUMAN_REVIEW_REQUIRED"            # an analyst must decide
SAR_PENDING = "SAR_PENDING_MLRO_APPROVAL"          # agent recommends a SAR; the MLRO decides


@dataclass
class Decision:
    alert_id: str
    disposition: str
    typologies: list[str]
    confidence: float
    risk_indicators: list[str]
    evidence_txn_ids: list[str]
    rationale: str
    narrative: str
    agent: str = ""
    model: str = ""
    # v3: what should happen to the account, and why each item was flagged
    account_action: str = "NO_ACTION"            # NO_ACTION | REQUEST_DOCUMENTS | RESTRICT | CLOSE_ACCOUNT ("ban")
    account_action_reason: str = ""
    documents_requested: list[str] = field(default_factory=list)
    sar_reasoning: str = ""                      # whether a SAR is needed, why, and when
    flagged_items: list[dict] = field(default_factory=list)   # {id, kind, reason}
    risk_score: float | None = None              # probability the activity is suspicious, for ROC/AUC

    def validate(self) -> list[str]:
        errs = []
        if self.disposition not in DISPOSITIONS:
            errs.append(f"invalid disposition {self.disposition!r}")
        bad = [t for t in self.typologies if t not in TYPOLOGIES]
        if bad:
            errs.append(f"invalid typologies {bad}")
        if not 0.0 <= float(self.confidence) <= 1.0:
            errs.append("confidence outside [0,1]")
        if self.account_action not in ACCOUNT_ACTIONS:
            errs.append(f"invalid account_action {self.account_action!r}")
        return errs

    @property
    def is_escalation(self) -> bool:
        return self.disposition != "CLOSE_FALSE_POSITIVE"

    @property
    def score(self) -> float:
        """Suspicion score for ranking/AUC: the agent's own if given, else derived from disposition and confidence."""
        if self.risk_score is not None:
            return float(self.risk_score)
        c = float(self.confidence)
        return {"RECOMMEND_SAR": 0.5 + c / 2, "ESCALATE_L2": 0.3 + c * 0.4}.get(self.disposition, (1 - c) * 0.5)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class GuardrailResult:
    final_status: str
    final_disposition: str
    checks: list[dict] = field(default_factory=list)   # {name, passed, detail}
    overrides: list[str] = field(default_factory=list)

    @property
    def violations(self) -> list[dict]:
        return [c for c in self.checks if not c["passed"]]

    def to_dict(self) -> dict:
        return asdict(self) | {"violations": self.violations}
