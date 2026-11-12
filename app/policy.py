"""
Policy engine (Topics 7 and 8).

Plain code. No AI. Clever words in a prompt can't change it.

Tiers:
  TIER_1 -> agent can do it (logged)
  TIER_2 -> a human analyst must approve
  NEVER  -> always blocked (deny by default)
"""
from app import config

TIER_1 = "TIER_1"
TIER_2 = "TIER_2"
NEVER = "NEVER"


def get_risk(fraud_score: float | None) -> str:
    # Unknown score = treat as HIGH (safe side)
    if fraud_score is None:
        return "HIGH"
    return "HIGH" if fraud_score >= config.HIGH_RISK_SCORE else "LOW"


def get_tier(action: str, fraud_score: float | None) -> str:
    risk = get_risk(fraud_score)

    if action == "call_customer":
        return TIER_1                              # small, easy to undo
    if action == "close_alert":
        return TIER_2 if risk == "HIGH" else TIER_1
    if action == "freeze_account":
        return TIER_2                              # affects a real customer
    if action == "needs_review":
        return TIER_2                              # unsure -> human looks
    return NEVER                                   # deny by default
