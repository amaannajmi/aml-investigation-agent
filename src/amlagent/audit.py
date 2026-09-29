"""Append-only audit trail: one JSONL file per alert per run.

Every tool call (inputs, a digest of outputs, duration), the agent's decision
and the guardrail outcome are recorded, so any case can be reconstructed.
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from . import config as C


def _default(o):
    if hasattr(o, "isoformat"):
        return o.isoformat()
    return str(o)


class AuditLog:
    def __init__(self, run_id: str, alert_id: str, root: Path = C.AUDIT_DIR, enabled: bool = True):
        self.path = root / run_id / f"{alert_id}.jsonl"
        self.enabled = enabled
        self.events: list[dict] = []
        if enabled:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text("")

    def log(self, event: str, **payload) -> None:
        rec = {"ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), "event": event, **payload}
        self.events.append(rec)
        if self.enabled:
            with self.path.open("a") as f:
                f.write(json.dumps(rec, default=_default) + "\n")

    def tool_call(self, tb, name: str, args: dict | None = None):
        """Call a Toolbox method and log it. Returns the tool's result."""
        args = args or {}
        t0 = time.perf_counter()
        try:
            result = getattr(tb, name)(**args)
            err = None
        except Exception as e:  # tool errors are returned to the agent, and logged
            result, err = {"error": f"{type(e).__name__}: {e}"}, str(e)
        blob = json.dumps(result, default=_default, sort_keys=True)
        self.log("tool_call", tool=name, args=args, duration_ms=round((time.perf_counter() - t0) * 1000, 1),
                 result_sha256=hashlib.sha256(blob.encode()).hexdigest()[:16], result_bytes=len(blob), error=err)
        return result
