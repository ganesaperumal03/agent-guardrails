"""
Bank tools (Topic 2 and Topic 20).

These are normal Python functions. They are the ONLY code that reads the bank data.
The same functions are offered:
  - directly (USE_MCP=false), or
  - through MCP servers (USE_MCP=true, see mcp_servers/)

Safety inside every READ tool:
  1. Check the input format (stops "C101 OR 1=1" or "all")
  2. Limit the size (max 200 rows)
  3. Send only the fields an agent needs (no phone, address, Aadhaar)
  4. Log with the trace ID

ACTION tools (freeze, call, close) are NOT given to agents.
Only the `act` step of the workflow can use them, after policy + human approval.
"""
import difflib
import re

from app import config
from app.audit import log
from app import data_store

CUSTOMER_ID = re.compile(r"^C\d{3,10}$")
ACCOUNT_ID = re.compile(r"^ACC-[A-Z0-9]{1,20}$")

# The only profile fields an agent is allowed to see
SAFE_PROFILE_FIELDS = ["occupation", "monthly_income", "kyc_status",
                       "account_age_days", "risk_category"]


class ToolInputError(ValueError):
    """Raised when a tool gets a badly formatted input."""


def _check(pattern, value, what, trace_id, tool):
    if not isinstance(value, str) or not pattern.fullmatch(value):
        log(trace_id, "bank-system", "bad_tool_input", {"tool": tool, "field": what})
        raise ToolInputError(f"Invalid {what}")


# ======================= READ TOOLS (Tier 0) =======================

def get_transaction_history(customer_id: str, trace_id: str) -> dict:
    """Ledger: last payments of a customer (max 200 rows). Read only."""
    _check(CUSTOMER_ID, customer_id, "customer_id", trace_id, "get_transaction_history")
    rows = data_store.get_transactions(customer_id, config.MAX_TX_ROWS)
    log(trace_id, "bank-system:ledger", "tool_called",
        {"tool": "get_transaction_history", "rows": len(rows)})
    return {"transactions": rows}


def check_fraud_score(account: str, trace_id: str) -> dict:
    """Fraud API: risk score (0 to 1) of the receiving account. Read only."""
    _check(ACCOUNT_ID, account, "account", trace_id, "check_fraud_score")
    score = data_store.get_receiver_score(account)
    log(trace_id, "bank-system:fraud-api", "tool_called", {"tool": "check_fraud_score"})
    return {"score": score}


def get_customer_profile(customer_id: str, trace_id: str) -> dict:
    """Customer DB: KYC profile with ONLY safe fields. Read only."""
    _check(CUSTOMER_ID, customer_id, "customer_id", trace_id, "get_customer_profile")
    full = data_store.get_customer(customer_id)
    safe = {k: full.get(k) for k in SAFE_PROFILE_FIELDS}   # phone/address never leave
    log(trace_id, "bank-system:screening", "tool_called", {"tool": "get_customer_profile"})
    return safe


def screen_customer(customer_id: str, trace_id: str) -> dict:
    """
    Screening engine: is the customer's name close to a watchlist name?
    The NAME stays inside this tool. The agent only gets "clear" or "possible_match".
    """
    _check(CUSTOMER_ID, customer_id, "customer_id", trace_id, "screen_customer")
    name = (data_store.get_customer(customer_id).get("name") or "").lower()
    best = max((difflib.SequenceMatcher(None, name, w.lower()).ratio()
                for w in data_store.get_watchlist()), default=0.0)
    result = "possible_match" if best >= 0.85 else "clear"
    log(trace_id, "bank-system:screening", "tool_called", {"tool": "screen_customer"})
    return {"result": result}


# ======================= ACTION TOOLS (only for `act`) =======================
# Fake versions: they only write to the audit log.

def freeze_account(customer_id: str, trace_id: str) -> None:
    log(trace_id, "bank-system:core", "account_frozen", {"customer_id": customer_id})


def call_customer(customer_id: str, trace_id: str) -> None:
    # The real system would fetch the phone number itself. The AI never sees it.
    log(trace_id, "bank-system:crm", "call_task_created", {"customer_id": customer_id})


def close_alert(customer_id: str, trace_id: str) -> None:
    log(trace_id, "bank-system:alerts", "alert_closed", {"customer_id": customer_id})


ACTION_TOOLS = {
    "freeze_account": freeze_account,
    "call_customer": call_customer,
    "close_alert": close_alert,
}
