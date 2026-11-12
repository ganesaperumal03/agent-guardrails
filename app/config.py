"""
All settings in one place.

Change them in a `.env` file (copy `.env.example` to `.env`).
"""
import os
from pathlib import Path

# Folder of the whole project (agent-guardrails/)
ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv():
    """Tiny .env reader, so we don't need an extra library."""
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()

# ---------------- Database ----------------
# postgres = real database (bank data, audit log, cases, saved LangGraph notebooks)
# memory   = JSON files + in-memory (used by tests and the red-team)
DATA_BACKEND = os.getenv("DATA_BACKEND", "memory").lower()
DATABASE_URL = os.getenv("DATABASE_URL",
                         "postgresql://novapay:novapay@localhost:5432/novapay")
# SQLAlchemy needs to be told which driver to use: "postgresql+psycopg://" = psycopg 3.
# (The LangGraph checkpointer pool uses DATABASE_URL as it is.)
SQLALCHEMY_URL = (DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)
                  if DATABASE_URL.startswith("postgresql://") else DATABASE_URL)

# ---------------- LLM (AWS Bedrock) ----------------
# none    = template mode: simple rules write the report. No AWS needed.
# bedrock = Qwen on AWS Bedrock writes the report (boto3, Converse API).
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "none").lower()
AWS_REGION = os.getenv("AWS_REGION", "ap-south-1")              # Mumbai
BEDROCK_MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "qwen.qwen3-32b-v1:0")
# Price per 1,000 tokens in USD. Check the AWS Bedrock pricing page and update.
PRICE_IN_PER_1K = float(os.getenv("PRICE_IN_PER_1K", "0.00015"))
PRICE_OUT_PER_1K = float(os.getenv("PRICE_OUT_PER_1K", "0.0006"))

# ---------------- MCP ----------------
# false = agents call tool functions directly
# true  = agents call tools through the 3 MCP servers (start them first!)
USE_MCP = os.getenv("USE_MCP", "false").lower() == "true"
MCP_URLS = {
    "ledger": os.getenv("MCP_LEDGER_URL", "http://127.0.0.1:8001/mcp"),
    "fraud": os.getenv("MCP_FRAUD_URL", "http://127.0.0.1:8002/mcp"),
    "screening": os.getenv("MCP_SCREENING_URL", "http://127.0.0.1:8003/mcp"),
}

# ---------------- Audit log (memory mode only) ----------------
AUDIT_FILE = os.getenv("AUDIT_FILE", str(ROOT / "logs" / "audit.jsonl"))

# ---------------- Safety numbers ----------------
HIGH_RISK_SCORE = 0.7     # receiver fraud score at or above this = HIGH risk
MAX_TX_ROWS = 200         # ledger never returns more rows than this
MIN_HISTORY = 3           # fewer past payments than this = "not enough history"

# Only these analysts can approve. Anyone else = auto reject.
KNOWN_ANALYSTS = {"priya", "arjun", "kavya"}


def use_postgres() -> bool:
    return DATA_BACKEND == "postgres"
