"""
SQLAlchemy models = the database tables, written as Python classes.

Why this file exists:
  - One class = one table. One attribute = one column.
  - Alembic reads `Base.metadata` to know what the tables should look like.
  - The rest of the app uses these classes with select() / insert() / session.add()
    instead of writing SQL strings by hand.

Table names, column names and column types are the same as the old SCHEMA string
in app/db.py. (The append-only trigger on audit_log is NOT here: SQLAlchemy models
can't describe triggers, so it lives in the first Alembic migration.)

Style: SQLAlchemy 2.0 "typed" models.
  Mapped[int]        -> NOT NULL column
  Mapped[str | None] -> nullable column
  mapped_column(...) -> extra column settings (primary key, default, type ...)
"""
from datetime import datetime

from sqlalchemy import (BigInteger, Boolean, DateTime, Double, ForeignKey, Index,
                        Integer, Text, func, text)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Parent of every model. Base.metadata knows all the tables."""

    # Python `str` -> Postgres TEXT (the old schema used TEXT everywhere)
    type_annotation_map = {str: Text}

    def to_dict(self) -> dict:
        """One row as a plain dict: {column_name: value}."""
        return {attr.key: getattr(self, attr.key) for attr in self.__mapper__.column_attrs}


# ======================= BANK DATA (fake data from data/*.json) =======================

class Customer(Base):
    __tablename__ = "customers"

    customer_id: Mapped[str] = mapped_column(primary_key=True)
    name: Mapped[str | None]
    occupation: Mapped[str | None]
    monthly_income: Mapped[int | None] = mapped_column(Integer)
    kyc_status: Mapped[str | None]
    account_age_days: Mapped[int | None] = mapped_column(Integer)
    risk_category: Mapped[str | None]
    phone: Mapped[str | None]         # private: tools never send this to agents
    address: Mapped[str | None]       # private
    aadhaar: Mapped[str | None]       # private (stored masked)
    kyc_doc_text: Mapped[str | None]  # private


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (Index("tx_customer_idx", "customer_id", "id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.customer_id"))
    amount: Mapped[int] = mapped_column(BigInteger)
    to_account: Mapped[str | None]
    to_name: Mapped[str | None]
    tx_time: Mapped[str | None]
    note: Mapped[str | None]
    direction: Mapped[str] = mapped_column(server_default="out")


class Receiver(Base):
    __tablename__ = "receivers"

    account: Mapped[str] = mapped_column(primary_key=True)
    score: Mapped[float] = mapped_column(Double)   # DOUBLE PRECISION


class Watchlist(Base):
    __tablename__ = "watchlist"

    name: Mapped[str] = mapped_column(primary_key=True)


class Alert(Base):
    __tablename__ = "alerts"

    alert_id: Mapped[str] = mapped_column(primary_key=True)
    customer_id: Mapped[str]
    amount: Mapped[int] = mapped_column(BigInteger)
    to_account: Mapped[str]
    alert_time: Mapped[str]
    rule_hit: Mapped[str | None]
    story: Mapped[str | None]
    expected_action: Mapped[str | None]
    expected_escalation: Mapped[bool | None] = mapped_column(Boolean)


# ======================= CASES (the analyst queue) =======================

class Case(Base):
    __tablename__ = "cases"

    trace_id: Mapped[str] = mapped_column(primary_key=True)
    alert_id: Mapped[str | None]
    customer_id: Mapped[str | None]
    source: Mapped[str | None]               # api / eval / redteam
    status: Mapped[str | None]               # waiting_for_human / completed
    tier: Mapped[str | None]
    recommended_action: Mapped[str | None]
    analyst: Mapped[str | None]
    outcome: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ======================= AUDIT LOG (append-only) =======================

class AuditLog(Base):
    """Append-only: a trigger (see the first Alembic migration) blocks UPDATE/DELETE/TRUNCATE."""
    __tablename__ = "audit_log"
    __table_args__ = (Index("audit_trace_idx", "trace_id", "id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    trace_id: Mapped[str]
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    who: Mapped[str]
    event: Mapped[str]
    data: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
