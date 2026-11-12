"""
Service layer: simple functions the API, red-team and evals all use.

  start_case(alert)            -> runs the graph until it finishes or pauses for a human
  get_case(trace_id)           -> current status of a case
  submit_decision(trace_id, d) -> analyst's answer, the graph resumes
  list_cases(status)           -> the analyst queue (from the `cases` table)

Postgres mode: the full case state lives in LangGraph's checkpoint tables,
and a short summary row is kept in sync in the `cases` table after every step.
"""
from datetime import datetime, timezone

from langgraph.types import Command

from app import config
from app.audit import new_trace_id
from app.graph import build_graph

_graph = None
_memory_cases: dict[str, dict] = {}   # memory mode only


def graph():
    """Build the graph once, the first time it's needed."""
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


def _config(trace_id: str) -> dict:
    # thread_id = which saved notebook. We use the trace ID: 1 case = 1 notebook = 1 trace.
    return {"configurable": {"thread_id": trace_id}}


# ======================= CASES TABLE (keep in sync) =======================

def _save_case_row(case: dict, source: str | None = None) -> None:
    row = {
        "trace_id": case["trace_id"], "alert_id": case["alert_id"],
        "customer_id": case["customer_id"], "status": case["status"], "tier": case["tier"],
        "recommended_action": (case["report"] or {}).get("recommended_action"),
        "analyst": case.get("analyst"), "outcome": case["outcome"],
    }
    if config.use_postgres():
        from sqlalchemy import func
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        from app.db import get_session
        from app.models import Case

        # "Upsert": insert the row, or if this trace_id exists already, update it.
        # (`source` is NOT in the update list: it is set once, when the case starts.)
        stmt = pg_insert(Case).values(**row, source=source or "api")
        stmt = stmt.on_conflict_do_update(
            index_elements=[Case.trace_id],
            set_={"status": stmt.excluded.status, "tier": stmt.excluded.tier,
                  "recommended_action": stmt.excluded.recommended_action,
                  "analyst": stmt.excluded.analyst, "outcome": stmt.excluded.outcome,
                  "updated_at": func.now()})
        with get_session() as session:
            session.execute(stmt)
    else:
        old = _memory_cases.get(case["trace_id"], {})
        _memory_cases[case["trace_id"]] = {
            **old, **row, "source": old.get("source") or source or "api",
            "updated_at": datetime.now(timezone.utc).isoformat()}


def list_cases(status: str | None = None, source: str | None = "api",
               limit: int = 50) -> list[dict]:
    """source='api' = real cases only. None = also eval and red-team cases."""
    if config.use_postgres():
        from sqlalchemy import select

        from app.db import get_session
        from app.models import Case

        stmt = select(Case).order_by(Case.created_at.desc()).limit(limit)
        if status is not None:        # None = no filter
            stmt = stmt.where(Case.status == status)
        if source is not None:
            stmt = stmt.where(Case.source == source)
        with get_session() as session:
            return [case.to_dict() for case in session.scalars(stmt).all()]
    rows = [r for r in _memory_cases.values()
            if (not status or r["status"] == status) and (not source or r["source"] == source)]
    return sorted(rows, key=lambda r: r["updated_at"], reverse=True)[:limit]


# ======================= CASE FLOW =======================

def start_case(alert: dict, source: str = "api") -> dict:
    trace_id = new_trace_id()
    graph().invoke({"trace_id": trace_id, "alert": alert, "steps": 0}, _config(trace_id))
    case = get_case(trace_id)
    _save_case_row(case, source)
    return case


def get_case(trace_id: str) -> dict:
    snap = graph().get_state(_config(trace_id))
    if not snap.values:
        raise KeyError(trace_id)

    v = snap.values
    waiting = "human" in (snap.next or ())
    screen = None
    for task in snap.tasks or []:
        for intr in getattr(task, "interrupts", ()) or ():
            screen = intr.value

    return {
        "trace_id": trace_id,
        "status": "waiting_for_human" if waiting else "completed",
        "alert_id": v["alert"]["alert_id"],
        "customer_id": v["alert"]["customer_id"],
        "report": v.get("report"),
        "tier": v.get("tier"),
        "analyst": v.get("analyst"),
        "human_decision": v.get("human_decision"),
        "human_reason": v.get("human_reason"),
        "outcome": v.get("outcome"),
        "steps": v.get("steps", 0),
        "llm_tokens": v.get("llm_tokens", 0),
        "llm_cost_usd": v.get("llm_cost_usd", 0.0),
        "approval_screen": screen if waiting else None,
        # findings are useful for evals / red-team (they hold no private data)
        "inv_findings": v.get("inv_findings"),
        "kyc_findings": v.get("kyc_findings"),
    }


def submit_decision(trace_id: str, decision: dict) -> dict:
    case = get_case(trace_id)
    if case["status"] != "waiting_for_human":
        raise ValueError("This case is not waiting for a decision")
    graph().invoke(Command(resume=decision), _config(trace_id))
    case = get_case(trace_id)
    _save_case_row(case)
    return case
