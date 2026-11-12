"""
Audit log (Topics 10 and 11).

- Postgres mode: every event is one row in the audit_log table.
  A database trigger blocks UPDATE / DELETE / TRUNCATE (append-only).
- Memory mode: every event is one line of JSON in logs/audit.jsonl
- We only ADD events. We never edit or delete.
- Every line has a trace_id, so one case can be pulled out and replayed.
- Private data (phone, Aadhaar, email) is masked before writing.
"""
import json
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app import config

_lock = threading.Lock()

AADHAAR = re.compile(r"\b\d{4}[ -]?\d{4}[ -]?(\d{4})\b")
PHONE = re.compile(r"\b[6-9]\d{5}(\d{4})\b")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


def new_trace_id() -> str:
    return "tr-" + uuid.uuid4().hex[:8]


def mask_text(text: str) -> str:
    text = AADHAAR.sub(r"XXXX-XXXX-\1", text)
    text = PHONE.sub(r"XXXXXX\1", text)
    text = EMAIL.sub("[email removed]", text)
    return text


def mask(value):
    """Mask private data inside any string, list or dict."""
    if isinstance(value, str):
        return mask_text(value)
    if isinstance(value, list):
        return [mask(v) for v in value]
    if isinstance(value, dict):
        return {k: mask(v) for k, v in value.items()}
    return value


def log(trace_id: str, who: str, event: str, data: dict | None = None) -> None:
    if config.use_postgres():
        from app.db import get_session
        from app.models import AuditLog
        with get_session() as session:
            session.add(AuditLog(trace_id=trace_id, who=who, event=event,
                                 data=mask(data or {})))   # JSONB column takes a dict
        return

    line = {
        "trace_id": trace_id,
        "time": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "who": who,
        "event": event,
        "data": mask(data or {}),
    }
    path = Path(config.AUDIT_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock, open(path, "a", encoding="utf-8") as f:   # "a" = append only
        f.write(json.dumps(line, ensure_ascii=False) + "\n")


def read_trace(trace_id: str) -> list[dict]:
    """All events of one case, in order. READ ONLY - used for replay."""
    if config.use_postgres():
        from sqlalchemy import select

        from app.db import get_session
        from app.models import AuditLog
        with get_session() as session:
            rows = session.scalars(select(AuditLog).where(AuditLog.trace_id == trace_id)
                                   .order_by(AuditLog.id)).all()
            return [{"trace_id": r.trace_id, "time": r.ts.isoformat(timespec="milliseconds"),
                     "who": r.who, "event": r.event, "data": r.data} for r in rows]

    path = Path(config.AUDIT_FILE)
    if not path.exists():
        return []
    events = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if trace_id in line:
                item = json.loads(line)
                if item["trace_id"] == trace_id:
                    events.append(item)
    return events
