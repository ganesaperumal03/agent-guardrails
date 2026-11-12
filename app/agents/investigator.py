"""
Investigator agent (Topic 15): looks at the MONEY.

Tools: get_transaction_history, check_fraud_score (both read only)

Design:
  - Code does the maths (no AI): same answer every time, can't be tricked.
  - Raw UPI notes are NEVER passed forward. Only a True/False flag.
  - Output has a fixed shape (Pydantic). Extra fields are NOT allowed.
"""
import re
from statistics import mean
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app import config
from app.audit import log
from app.gateway import call_tool


class InvestigatorFindings(BaseModel):
    model_config = ConfigDict(extra="forbid")   # unknown fields = rejected

    agent: Literal["investigator"] = "investigator"
    tx_count: int
    receiver_fraud_score: float = Field(ge=0, le=1)
    usual_amount: int | None
    times_bigger: float | None
    new_receiver: bool
    night_transaction: bool
    suspicious_notes_found: bool
    risk_flags: list[str]


# ---------- note checking (one extra layer, NOT the main defence) ----------
SUSPICIOUS_WORDS = ["ignore", "system", "instruction", "freeze", "admin", "override",
                    "false alarm", "mark this", "close it", "approved by", "send to",
                    "verified safe", "recommended_action", "include"]
LONG_TOKEN = re.compile(r"[A-Za-z0-9+/=]{16,}")   # looks like base64 / encoded text


def is_suspicious_note(note: str) -> bool:
    text = (note or "").lower()
    if any(word in text for word in SUSPICIOUS_WORDS):
        return True
    if len(text) > 40:                       # normal notes are short: "rent", "dinner"
        return True
    if any(ord(ch) > 127 for ch in text):    # other scripts (Hindi, Tamil...) -> human looks
        return True
    if LONG_TOKEN.search(note or "") or "{" in text:
        return True
    return False


def _is_alert_tx(t, alert):
    return (t.get("to") == alert["to_account"] and t.get("amount") == alert["amount"]
            and t.get("time") == alert["time"])


def run_investigator(alert: dict, trace_id: str) -> dict:
    history = call_tool("investigator", "get_transaction_history", trace_id,
                        customer_id=alert["customer_id"])["transactions"]
    raw_score = call_tool("investigator", "check_fraud_score", trace_id,
                          account=alert["to_account"])["score"]

    # A tool result is untrusted too: it must be a real number
    if isinstance(raw_score, bool) or not isinstance(raw_score, (int, float)):
        raise ValueError("fraud score is not a number")
    score = float(raw_score)

    # Past OUTGOING payments, without the alert payment itself
    past_out = [t for t in history
                if t.get("direction", "out") == "out" and not _is_alert_tx(t, alert)]
    usual = mean(t["amount"] for t in past_out) if past_out else None
    times_bigger = round(alert["amount"] / usual, 1) if usual else None
    new_receiver = all(t.get("to") != alert["to_account"] for t in past_out)
    night = int(alert["time"][:2]) < 6
    suspicious = any(is_suspicious_note(t.get("note", "")) for t in history)

    flags = []
    if len(past_out) < config.MIN_HISTORY:
        flags.append("insufficient_history")
    if times_bigger is not None and times_bigger > 10:
        flags.append("amount_far_above_usual")
    if new_receiver:
        flags.append("new_receiver")
    if score >= config.HIGH_RISK_SCORE:
        flags.append("high_risk_receiver")
    if night:
        flags.append("night_transaction")
    if suspicious:
        flags.append("suspicious_note")

    findings = InvestigatorFindings(
        tx_count=len(history),
        receiver_fraud_score=score,
        usual_amount=round(usual) if usual else None,
        times_bigger=times_bigger,
        new_receiver=new_receiver,
        night_transaction=night,
        suspicious_notes_found=suspicious,
        risk_flags=flags,
    ).model_dump()

    log(trace_id, "investigator", "findings_ready", findings)
    return findings
