"""Initial schema: all tables + the append-only trigger on audit_log

This is the first migration. `upgrade()` builds the database from nothing;
`downgrade()` removes everything it built.

The tables match app/models.py. The trigger can't be described in a model, so it is
written here as plain SQL with op.execute(). It is the only raw SQL in the project.

Revision ID: 0001
Revises:
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---------------- bank data ----------------
    op.create_table(
        "customers",
        sa.Column("customer_id", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text()),
        sa.Column("occupation", sa.Text()),
        sa.Column("monthly_income", sa.Integer()),
        sa.Column("kyc_status", sa.Text()),
        sa.Column("account_age_days", sa.Integer()),
        sa.Column("risk_category", sa.Text()),
        sa.Column("phone", sa.Text()),          # private
        sa.Column("address", sa.Text()),        # private
        sa.Column("aadhaar", sa.Text()),        # private (stored masked)
        sa.Column("kyc_doc_text", sa.Text()),   # private
    )

    op.create_table(
        "transactions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("customer_id", sa.Text(), sa.ForeignKey("customers.customer_id"),
                  nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("to_account", sa.Text()),
        sa.Column("to_name", sa.Text()),
        sa.Column("tx_time", sa.Text()),
        sa.Column("note", sa.Text()),
        sa.Column("direction", sa.Text(), nullable=False, server_default="out"),
    )
    op.create_index("tx_customer_idx", "transactions", ["customer_id", "id"])

    op.create_table(
        "receivers",
        sa.Column("account", sa.Text(), primary_key=True),
        sa.Column("score", sa.Double(), nullable=False),
    )

    op.create_table(
        "watchlist",
        sa.Column("name", sa.Text(), primary_key=True),
    )

    op.create_table(
        "alerts",
        sa.Column("alert_id", sa.Text(), primary_key=True),
        sa.Column("customer_id", sa.Text(), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("to_account", sa.Text(), nullable=False),
        sa.Column("alert_time", sa.Text(), nullable=False),
        sa.Column("rule_hit", sa.Text()),
        sa.Column("story", sa.Text()),
        sa.Column("expected_action", sa.Text()),
        sa.Column("expected_escalation", sa.Boolean()),
    )

    # ---------------- cases (analyst queue) ----------------
    op.create_table(
        "cases",
        sa.Column("trace_id", sa.Text(), primary_key=True),
        sa.Column("alert_id", sa.Text()),
        sa.Column("customer_id", sa.Text()),
        sa.Column("source", sa.Text()),               # api / eval / redteam
        sa.Column("status", sa.Text()),               # waiting_for_human / completed
        sa.Column("tier", sa.Text()),
        sa.Column("recommended_action", sa.Text()),
        sa.Column("analyst", sa.Text()),
        sa.Column("outcome", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )

    # ---------------- audit log ----------------
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("trace_id", sa.Text(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("who", sa.Text(), nullable=False),
        sa.Column("event", sa.Text(), nullable=False),
        sa.Column("data", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
    )
    op.create_index("audit_trace_idx", "audit_log", ["trace_id", "id"])

    # ---------------- append-only trigger (Topic 10) ----------------
    # Nobody can change or delete audit rows. Any UPDATE / DELETE / TRUNCATE
    # runs this function, which raises an error, so the statement fails.
    op.execute("""
        CREATE OR REPLACE FUNCTION audit_no_change() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'audit_log is append-only';
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER audit_no_update BEFORE UPDATE OR DELETE ON audit_log
            FOR EACH ROW EXECUTE FUNCTION audit_no_change()
    """)
    op.execute("""
        CREATE TRIGGER audit_no_truncate BEFORE TRUNCATE ON audit_log
            FOR EACH STATEMENT EXECUTE FUNCTION audit_no_change()
    """)


def downgrade() -> None:
    # Remove the trigger first, then the tables (reverse order of upgrade).
    op.execute("DROP TRIGGER IF EXISTS audit_no_truncate ON audit_log")
    op.execute("DROP TRIGGER IF EXISTS audit_no_update ON audit_log")
    op.execute("DROP FUNCTION IF EXISTS audit_no_change()")

    op.drop_index("audit_trace_idx", table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_table("cases")
    op.drop_table("alerts")
    op.drop_table("watchlist")
    op.drop_table("receivers")
    op.drop_index("tx_customer_idx", table_name="transactions")
    op.drop_table("transactions")
    op.drop_table("customers")
