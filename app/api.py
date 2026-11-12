"""
FastAPI app. Open Swagger at:  http://127.0.0.1:8000/docs

Run:  uvicorn app.api:app --reload
"""
from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app import config, service
from app.audit import read_trace
from app.data_store import list_alerts as all_alerts

app = FastAPI(
    title="NovaPay Agent Guardrails",
    description="Fraud alert triage with specialist agents, a policy engine, "
                "human approval and an audit log.",
    version="1.0.0",
)


# ======================= REQUEST MODELS =======================

class Alert(BaseModel):
    alert_id: str = Field(pattern=r"^A-\d{3,8}$", examples=["A-2001"])
    customer_id: str = Field(max_length=30, examples=["C101"])
    amount: int = Field(gt=0, le=100_000_000, examples=[200000])
    to_account: str = Field(max_length=30, examples=["ACC-9988"])
    time: str = Field(pattern=r"^\d{2}:\d{2}$", examples=["02:14"])
    rule_hit: str = Field(default="", max_length=200, examples=["Large amount + night"])


class StartCase(BaseModel):
    alert_id: str | None = Field(default=None, examples=["A-1001"],
                                 description="Use a sample alert from GET /alerts")
    alert: Alert | None = Field(default=None, description="Or send your own alert")


class Decision(BaseModel):
    analyst: str = Field(examples=["priya"], description="Known: priya, arjun, kavya")
    decision: Literal["approve", "reject", "edit"]
    reason: str = Field(default="", examples=["Night transfer 32x usual to mule account"])
    new_action: str | None = Field(default=None, examples=[None],
                                   description="Only for 'edit'. Try 'delete_customer' to see the block!")


# ======================= ENDPOINTS =======================

@app.get("/health", tags=["0. Info"])
def health():
    db = "not used"
    if config.use_postgres():
        from app.db import ping
        try:
            ping()
            db = "connected"
        except Exception as e:
            db = f"error: {type(e).__name__}"
    return {"status": "ok", "data_backend": config.DATA_BACKEND, "database": db,
            "llm": f"bedrock:{config.BEDROCK_MODEL_ID}" if config.LLM_PROVIDER == "bedrock"
                   else "none (template mode)",
            "use_mcp": config.USE_MCP}


@app.get("/alerts", tags=["1. Alerts"])
def list_alerts():
    """Sample alerts from the Week 7 fraud engine (with their story)."""
    return [{"alert_id": a["alert_id"], "customer_id": a["customer_id"],
             "amount": a["amount"], "time": a["time"], "story": a["story"]}
            for a in all_alerts()]


@app.post("/cases", tags=["2. Cases"])
def start_case(body: StartCase):
    """Start triage. Runs all agents, then stops for a human if needed."""
    if body.alert:
        alert = body.alert.model_dump()
    elif body.alert_id:
        found = [a for a in all_alerts() if a["alert_id"] == body.alert_id]
        if not found:
            raise HTTPException(404, "Unknown alert_id")
        alert = Alert(**found[0]).model_dump()   # drops the 'expected_*' answer fields
    else:
        raise HTTPException(400, "Send alert_id or alert")
    return service.start_case(alert)


@app.get("/cases", tags=["2. Cases"])
def list_cases(status: Literal["waiting_for_human", "completed"] | None = None,
               source: Literal["api", "eval", "redteam"] | None = "api", limit: int = 50):
    """The analyst queue. Try status = waiting_for_human. (Postgres: survives restarts.)"""
    return service.list_cases(status, source, min(limit, 200))


@app.get("/cases/{trace_id}", tags=["2. Cases"])
def get_case(trace_id: str):
    try:
        return service.get_case(trace_id)
    except KeyError:
        raise HTTPException(404, "Case not found")


@app.post("/cases/{trace_id}/decision", tags=["2. Cases"])
def decide(trace_id: str, body: Decision):
    """The analyst approves, rejects or edits. The case then continues."""
    try:
        return service.submit_decision(trace_id, body.model_dump())
    except KeyError:
        raise HTTPException(404, "Case not found")
    except ValueError as e:
        raise HTTPException(409, str(e))


@app.get("/cases/{trace_id}/replay", tags=["3. Audit"])
def replay(trace_id: str):
    """Every event of one case, in order. READ ONLY: never calls any tool again."""
    events = read_trace(trace_id)
    if not events:
        raise HTTPException(404, "No events for this trace ID")
    return {"trace_id": trace_id, "event_count": len(events), "events": events}


@app.post("/redteam/run", tags=["4. Testing"])
def run_redteam():
    """Run all attacks in redteam/attacks.json and report what happened."""
    from redteam.runner import run_all
    return run_all()


@app.post("/evals/run", tags=["4. Testing"])
def run_evals():
    """Run all sample alerts and measure the agents."""
    from evals.run_evals import run_evals as _run
    return _run()
