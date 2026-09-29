"""The decision contract every agent must return."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

DISPOSITIONS = ("CLOSE_FALSE_POSITIVE", "ESCALATE_L2", "RECOMMEND_SAR")
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

    def validate(self) -> list[str]:
        errs = []
        if self.disposition not in DISPOSITIONS:
            errs.append(f"invalid disposition {self.disposition!r}")
        bad = [t for t in self.typologies if t not in TYPOLOGIES]
        if bad:
            errs.append(f"invalid typologies {bad}")
        if not 0.0 <= float(self.confidence) <= 1.0:
            errs.append("confidence outside [0,1]")
        return errs

    @property
    def is_escalation(self) -> bool:
        return self.disposition != "CLOSE_FALSE_POSITIVE"

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
