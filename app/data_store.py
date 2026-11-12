"""
Data layer: the ONLY place that knows where bank data lives.

Two backends (set DATA_BACKEND in .env):
  postgres -> real database (see app/db.py)
  memory   -> JSON files loaded into Python dicts (tests and red-team)

tools.py calls these functions. It does not care which backend is used.
"""
import copy
import json
from contextlib import contextmanager

from app import config
from app.config import ROOT

DATA_DIR = ROOT / "data"


def _load(name):
    with open(DATA_DIR / f"{name}.json", encoding="utf-8") as f:
        return json.load(f)


# In-memory copy (always loaded: tests, red-team, and as a simple fallback)
DB = {
    "customers": _load("customers"),        # customer_id -> profile
    "transactions": _load("transactions"),  # customer_id -> list of payments
    "receivers": _load("receivers"),        # account -> {"score": ...}
    "watchlist": _load("watchlist"),        # list of watchlist names
    "alerts": _load("alerts"),              # sample alerts from the Week 7 engine
}


_force_memory = False   # set by temporary_changes() during red-team attacks


def _pg():
    return config.use_postgres() and not _force_memory


# ======================= READ FUNCTIONS =======================

def get_transactions(customer_id: str, limit: int) -> list[dict]:
    if _pg():
        from sqlalchemy import select

        from app.db import get_session
        from app.models import Transaction
        with get_session() as session:
            # newest N rows first (LIMIT), then flip them so the oldest comes first
            rows = session.scalars(select(Transaction)
                                   .where(Transaction.customer_id == customer_id)
                                   .order_by(Transaction.id.desc()).limit(limit)).all()
            return [{"amount": r.amount, "to": r.to_account, "to_name": r.to_name,
                     "time": r.tx_time, "note": r.note, "direction": r.direction}
                    for r in reversed(rows)]
    return DB["transactions"].get(customer_id, [])[-limit:]


def get_receiver_score(account: str):
    if _pg():
        from sqlalchemy import select

        from app.db import get_session
        from app.models import Receiver
        with get_session() as session:
            score = session.scalar(select(Receiver.score).where(Receiver.account == account))
            return score if score is not None else 0.0
    return DB["receivers"].get(account, {}).get("score", 0.0)


def get_customer(customer_id: str) -> dict:
    """Full profile (with private fields). Only tools.py may call this."""
    if _pg():
        from app.db import get_session
        from app.models import Customer
        with get_session() as session:
            customer = session.get(Customer, customer_id)   # look up by primary key
            return customer.to_dict() if customer else {}
    return DB["customers"].get(customer_id, {})


def get_watchlist() -> list[str]:
    if _pg():
        from sqlalchemy import select

        from app.db import get_session
        from app.models import Watchlist
        with get_session() as session:
            return list(session.scalars(select(Watchlist.name)).all())
    return DB["watchlist"]


def list_alerts() -> list[dict]:
    """Alerts with the same keys as data/alerts.json."""
    if _pg():
        from sqlalchemy import select

        from app.db import get_session
        from app.models import Alert
        with get_session() as session:
            rows = session.scalars(select(Alert).order_by(Alert.alert_id)).all()
            return [{"alert_id": r.alert_id, "customer_id": r.customer_id,
                     "amount": r.amount, "to_account": r.to_account, "time": r.alert_time,
                     "rule_hit": r.rule_hit, "story": r.story,
                     "expected_action": r.expected_action,
                     "expected_escalation": r.expected_escalation} for r in rows]
    return DB["alerts"]


# ======================= RED-TEAM HELPER =======================

@contextmanager
def temporary_changes():
    """
    Red-team only: switch to MEMORY data, change it for one attack, then put it back.
    The real Postgres data is never touched by attacks.
    """
    global _force_memory
    backup = copy.deepcopy(DB)
    _force_memory = True        # only bank DATA switches; audit and cases stay in Postgres
    try:
        yield DB
    finally:
        for key in DB:
            DB[key] = backup[key]
        _force_memory = False
