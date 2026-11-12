"""
PostgreSQL setup, with SQLAlchemy 2.0 (queries) and Alembic (table creation).

Start Postgres:   docker compose up -d
Create + fill:    python -m app.db           (safe to run again)
Start fresh:      python -m app.db --reset   (reloads bank data; audit log is kept)

What `python -m app.db` does:
  1. `alembic upgrade head`  -> creates / updates the tables (see alembic/versions/)
  2. fills the bank tables from data/*.json, but only if `customers` is empty

Tables (classes are in app/models.py):
  customers, transactions, receivers, watchlist, alerts  -> fake bank data
  cases       -> one row per case (status, tier, outcome) = the analyst queue
  audit_log   -> append-only: UPDATE / DELETE / TRUNCATE are blocked by a trigger
  (LangGraph also creates its own checkpoint tables for paused cases)

Usage in the app:
    from app.db import get_session
    with get_session() as session:
        session.execute(select(Customer))...
"""
import json
import sys
from contextlib import contextmanager

from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import sessionmaker

from app import config
from app.models import Alert, AuditLog, Case, Customer, Receiver, Transaction, Watchlist

# ======================= SQLALCHEMY: engine + session =======================

_engine = None
_SessionLocal = None


def get_engine():
    """One shared engine (it keeps its own small connection pool) for the whole process."""
    global _engine, _SessionLocal
    if _engine is None:
        _engine = create_engine(config.SQLALCHEMY_URL)
        # expire_on_commit=False: rows stay readable after the `with` block ends
        _SessionLocal = sessionmaker(_engine, expire_on_commit=False)
    return _engine


@contextmanager
def get_session():
    """
    Use it like this:

        with get_session() as session:
            session.add(...)

    Leaving the `with` block normally = COMMIT. An error inside = ROLLBACK, then re-raised.
    """
    get_engine()
    with _SessionLocal() as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


def ping() -> None:
    """Raises an error if the database can't be reached (used by GET /health)."""
    with get_session() as session:
        session.execute(select(1))


# ======================= PSYCOPG POOL: only for LangGraph =======================

_pool = None


def get_pool():
    """
    A psycopg ConnectionPool. The app does NOT use it for queries (SQLAlchemy does that).
    It exists ONLY because LangGraph's PostgresSaver (the checkpointer that saves paused
    cases, see app/graph.py) needs a psycopg pool and cannot use a SQLAlchemy session.
    """
    global _pool
    if _pool is None:
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool

        _pool = ConnectionPool(
            config.DATABASE_URL,
            min_size=1,
            max_size=10,
            kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
            open=True,
        )
    return _pool


# ======================= SETUP: migrate + seed =======================

def _load_json(name):
    with open(config.ROOT / "data" / f"{name}.json", encoding="utf-8") as f:
        return json.load(f)


def migrate():
    """Same as typing `alembic upgrade head` in the terminal."""
    from alembic import command
    from alembic.config import Config

    command.upgrade(Config(str(config.ROOT / "alembic.ini")), "head")


def seed():
    """Copy the JSON fake data into Postgres."""
    with get_session() as session:
        customers = _load_json("customers")
        session.execute(
            pg_insert(Customer).on_conflict_do_nothing(index_elements=["customer_id"]),
            [{"customer_id": cid, "name": p["name"], "occupation": p["occupation"],
              "monthly_income": p["monthly_income"], "kyc_status": p["kyc_status"],
              "account_age_days": p["account_age_days"], "risk_category": p["risk_category"],
              "phone": p["phone"], "address": p["address"], "aadhaar": p["aadhaar"],
              "kyc_doc_text": p["kyc_doc_text"]}
             for cid, p in customers.items()])

        tx_rows = [{"customer_id": cid, "amount": t["amount"], "to_account": t["to"],
                    "to_name": t.get("to_name", ""), "tx_time": t["time"],
                    "note": t.get("note", ""), "direction": t.get("direction", "out")}
                   for cid, rows in _load_json("transactions").items() for t in rows]
        if tx_rows:
            session.add_all([Transaction(**row) for row in tx_rows])

        receivers = [{"account": acc, "score": r["score"]}
                     for acc, r in _load_json("receivers").items()]
        if receivers:
            session.execute(pg_insert(Receiver).on_conflict_do_nothing(), receivers)

        watchlist = [{"name": name} for name in _load_json("watchlist")]
        if watchlist:
            session.execute(pg_insert(Watchlist).on_conflict_do_nothing(), watchlist)

        alerts = [{"alert_id": a["alert_id"], "customer_id": a["customer_id"],
                   "amount": a["amount"], "to_account": a["to_account"],
                   "alert_time": a["time"], "rule_hit": a["rule_hit"], "story": a["story"],
                   "expected_action": a["expected_action"],
                   "expected_escalation": a["expected_escalation"]}
                  for a in _load_json("alerts")]
        if alerts:
            session.execute(pg_insert(Alert).on_conflict_do_nothing(), alerts)


def reset_bank_data():
    """Empty the bank data tables. Never touches audit_log (and not cases)."""
    with get_session() as session:
        # transactions first: it points to customers (foreign key)
        for model in (Transaction, Customer, Receiver, Watchlist, Alert):
            session.execute(delete(model))


def init_db(reset: bool = False):
    migrate()
    if reset:
        reset_bank_data()
    with get_session() as session:
        has_customers = session.scalar(select(Customer.customer_id).limit(1)) is not None
    if not has_customers:
        seed()
        print("Seeded bank data from data/*.json")
    with get_session() as session:
        counts = {model.__tablename__: session.scalar(select(func.count()).select_from(model))
                  for model in (Customer, Transaction, Receiver, Alert, Case, AuditLog)}
    print("Tables ready:", counts)


if __name__ == "__main__":
    init_db(reset="--reset" in sys.argv)
