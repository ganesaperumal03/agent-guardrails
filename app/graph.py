"""
The LangGraph flow (Topics 12, 13, 18).

        START
          |
     orchestrator
       /       \\          (run at the same time)
 investigator   kyc
       \\       /          (wait for BOTH)
      case_writer
          |
        policy
     /    |     \\
   act  human  blocked
    |     |       |
   END   act     END
          |
         END

Rules we follow:
  - Routing is fixed CODE (arrows), not AI.
  - Messages from other agents are untrusted: re-checked with Pydantic.
  - If anything fails -> "needs_review" -> a human looks. Never guess.
  - The human node only: show -> wait -> read. It never DOES an action.
  - The act node checks the policy AGAIN before doing anything.
"""
import operator
from typing import Annotated, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app import config
from app.agents import case_writer, investigator, kyc
from app.agents.investigator import InvestigatorFindings
from app.agents.kyc import KycFindings
from app.audit import log
from app.policy import NEVER, TIER_2, get_tier
from app.tools import ACTION_TOOLS


# ======================= STATE (the shared notebook) =======================

class CaseState(TypedDict, total=False):
    trace_id: str
    alert: dict
    steps: Annotated[int, operator.add]   # every node adds 1 (used in evals)
    inv_findings: dict
    kyc_findings: dict
    report: dict
    llm_tokens: int
    llm_cost_usd: float
    tier: str
    analyst: str
    human_decision: str
    human_reason: str
    final_action: str
    outcome: str


FAILED = {"failed": True, "risk_flags": ["agent_failed"]}


# ======================= NODES =======================

def orchestrator(state: CaseState):
    log(state["trace_id"], "orchestrator", "case_started",
        {"alert_id": state["alert"]["alert_id"], "customer_id": state["alert"]["customer_id"]})
    return {"steps": 1}


def investigator_node(state: CaseState):
    try:
        found = investigator.run_investigator(state["alert"], state["trace_id"])
        # Treat the agent's message as untrusted: check the shape again
        found = InvestigatorFindings.model_validate(found).model_dump()
    except Exception as e:
        log(state["trace_id"], "orchestrator", "agent_failed",
            {"agent": "investigator", "error": type(e).__name__})
        found = {"agent": "investigator", **FAILED}
    return {"inv_findings": found, "steps": 1}


def kyc_node(state: CaseState):
    try:
        found = kyc.run_kyc(state["alert"], state["trace_id"])
        found = KycFindings.model_validate(found).model_dump()
    except Exception as e:
        log(state["trace_id"], "orchestrator", "agent_failed",
            {"agent": "kyc", "error": type(e).__name__})
        found = {"agent": "kyc", **FAILED}
    return {"kyc_findings": found, "steps": 1}


def case_writer_node(state: CaseState):
    inv, kyc_f = state["inv_findings"], state["kyc_findings"]
    tokens, cost = 0, 0.0
    if inv.get("failed") or kyc_f.get("failed"):
        report = {"summary": "A specialist agent failed. Manual review needed.",
                  "evidence": [], "recommended_action": "needs_review",
                  "confidence": "low", "missing_info": ["specialist agent failed"]}
        log(state["trace_id"], "case-writer", "report_ready",
            {"action": "needs_review", "reason": "agent_failed"})
    else:
        report, tokens, cost = case_writer.run_case_writer(state["alert"], inv, kyc_f,
                                                           state["trace_id"])
    return {"report": report, "llm_tokens": tokens, "llm_cost_usd": cost, "steps": 1}


def policy_node(state: CaseState):
    score = state["inv_findings"].get("receiver_fraud_score")   # None if agent failed
    action = state["report"]["recommended_action"]
    tier = get_tier(action, score)
    log(state["trace_id"], "policy-engine", "tier_decided", {"action": action, "tier": tier})
    return {"tier": tier, "steps": 1}


def choose_next(state: CaseState) -> str:
    """The 'if' on the arrow (conditional edge)."""
    if state["tier"] == NEVER:
        return "blocked"
    if state["tier"] == TIER_2:
        return "human"
    return "act"


def human_node(state: CaseState):
    # ---- show + wait ----  (code above interrupt runs again on resume, so keep it empty)
    answer = interrupt({
        "alert": state["alert"],
        "report": state["report"],
        "tier": state["tier"],
        "receiver_fraud_score": state["inv_findings"].get("receiver_fraud_score"),
        "choices": ["approve", "reject", "edit"],
    })

    # ---- read the answer (runs once, after the analyst replies) ----
    answer = answer if isinstance(answer, dict) else {}
    analyst = str(answer.get("analyst", "")).strip().lower()
    decision = answer.get("decision")
    reason = str(answer.get("reason") or "").strip()
    action = state["report"]["recommended_action"]

    if decision not in ("approve", "reject", "edit"):
        decision, reason = "reject", "Invalid decision"
    if analyst not in config.KNOWN_ANALYSTS:
        decision, reason = "reject", "Unknown analyst (auto reject)"
    if not reason:
        decision, reason = "reject", "No reason given (auto reject)"   # no rubber-stamping
    if decision == "edit":
        action = str(answer.get("new_action") or action)

    log(state["trace_id"], f"analyst:{analyst or 'unknown'}", "human_decision",
        {"decision": decision, "reason": reason, "action": action})
    return {"analyst": analyst, "human_decision": decision, "human_reason": reason,
            "final_action": action, "steps": 1}


def act_node(state: CaseState):
    trace_id = state["trace_id"]
    action = state.get("final_action") or state["report"]["recommended_action"]
    customer = state["alert"]["customer_id"]   # target ALWAYS comes from the alert, never from text

    # 1. Tier 2 needs a real approval
    if state["tier"] == TIER_2 and state.get("human_decision") not in ("approve", "edit"):
        log(trace_id, "orchestrator", "no_action", {"reason": "not approved"})
        return {"outcome": "no action (rejected by analyst)", "steps": 1}

    # 2. Check the policy AGAIN (an edit may have changed the action)
    known = action in ACTION_TOOLS or action == "needs_review"
    tier_now = get_tier(action, state["inv_findings"].get("receiver_fraud_score"))
    if not known or tier_now == NEVER:
        log(trace_id, "policy-engine", "blocked", {"action": action})
        return {"outcome": f"blocked: {action}", "steps": 1}

    if action == "needs_review":
        log(trace_id, "orchestrator", "no_action", {"reason": "manual review by analyst"})
        return {"outcome": "manual review (no automatic action)", "steps": 1}

    ACTION_TOOLS[action](customer, trace_id)
    log(trace_id, "orchestrator", "action_done", {"action": action, "customer_id": customer})
    return {"outcome": f"done: {action}", "steps": 1}


def blocked_node(state: CaseState):
    log(state["trace_id"], "policy-engine", "blocked",
        {"action": state["report"]["recommended_action"]})
    return {"outcome": "blocked by policy", "steps": 1}


# ======================= BUILD THE GRAPH =======================

def build_graph():
    g = StateGraph(CaseState)

    g.add_node("orchestrator", orchestrator)
    g.add_node("investigator", investigator_node)
    g.add_node("kyc", kyc_node)
    g.add_node("case_writer", case_writer_node)
    g.add_node("policy", policy_node)
    g.add_node("human", human_node)
    g.add_node("act", act_node)
    g.add_node("blocked", blocked_node)

    g.add_edge(START, "orchestrator")
    g.add_edge("orchestrator", "investigator")          # parallel
    g.add_edge("orchestrator", "kyc")                   # parallel
    g.add_edge(["investigator", "kyc"], "case_writer")  # wait for both
    g.add_edge("case_writer", "policy")
    g.add_conditional_edges("policy", choose_next,
                            {"human": "human", "act": "act", "blocked": "blocked"})
    g.add_edge("human", "act")
    g.add_edge("act", END)
    g.add_edge("blocked", END)

    return g.compile(checkpointer=make_checkpointer())


def make_checkpointer():
    """
    Checkpointer = saves the notebook so a case can pause and resume.
      postgres -> paused cases survive a server restart (production style)
      memory   -> lost on restart (tests only)
    """
    if config.use_postgres():
        from langgraph.checkpoint.postgres import PostgresSaver

        from app.db import get_pool
        saver = PostgresSaver(get_pool())
        saver.setup()          # creates LangGraph's own tables (safe to run again)
        return saver
    return InMemorySaver()
