"""
KYC agent (Topic 16): looks at the PERSON.

Tools: get_customer_profile, screen_customer (both read only)

Design:
  - Never sees phone, address, Aadhaar or the name (tools don't send them).
  - "possible_match" on screening is NOT "guilty". A human must check.
  - Only conclusions (labels) leave this agent.
"""
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.audit import log
from app.gateway import call_tool


class KycFindings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent: Literal["kyc"] = "kyc"
    kyc_status: Literal["verified", "pending", "expired", "unknown"]
    occupation: str
    account_age_days: int | None
    customer_risk_category: Literal["low", "medium", "high", "unknown"]
    amount_vs_income_months: float | None
    screening_result: Literal["clear", "possible_match"]
    risk_flags: list[str]


def _clean_word(text) -> str:
    """Occupation is customer-typed text: keep only letters/spaces, max 40 chars."""
    return re.sub(r"[^A-Za-z ]", "", str(text or ""))[:40].strip() or "unknown"


def _one_of(value, allowed):
    return value if value in allowed else "unknown"


def run_kyc(alert: dict, trace_id: str) -> dict:
    p = call_tool("kyc", "get_customer_profile", trace_id, customer_id=alert["customer_id"])
    screen = call_tool("kyc", "screen_customer", trace_id, customer_id=alert["customer_id"])

    income = p.get("monthly_income")
    months = round(alert["amount"] / income, 1) if income else None
    age = p.get("account_age_days")

    findings = KycFindings(
        kyc_status=_one_of(p.get("kyc_status"), {"verified", "pending", "expired"}),
        occupation=_clean_word(p.get("occupation")),
        account_age_days=age,
        customer_risk_category=_one_of(p.get("risk_category"), {"low", "medium", "high"}),
        amount_vs_income_months=months,
        screening_result=screen["result"],
        risk_flags=[],
    )

    if findings.kyc_status != "verified":
        findings.risk_flags.append("kyc_not_verified")
    if months is not None and months > 3:
        findings.risk_flags.append("amount_not_fit_income")
    if age is not None and age < 30:
        findings.risk_flags.append("new_account")
    if findings.customer_risk_category == "high":
        findings.risk_flags.append("high_risk_customer")
    if findings.screening_result == "possible_match":
        findings.risk_flags.append("screening_possible_match")

    result = findings.model_dump()
    log(trace_id, "kyc-agent", "findings_ready", result)
    return result
