"""
Tool gateway: the ONE door agents use to reach tools.

Two jobs:
  1. Least privilege: each agent can use ONLY its own tools.
  2. Switch: call tools directly, or through MCP servers (USE_MCP=true).
"""
import asyncio
import json

from app import config, tools
from app.audit import log

# Which agent may use which tool (Topic 14: least privilege)
AGENT_TOOLS = {
    "investigator": {"get_transaction_history", "check_fraud_score"},
    "kyc": {"get_customer_profile", "screen_customer"},
    "case_writer": set(),          # no tools at all
}

# Which MCP server offers which tool (Topic 20)
TOOL_SERVER = {
    "get_transaction_history": "ledger",
    "check_fraud_score": "fraud",
    "get_customer_profile": "screening",
    "screen_customer": "screening",
}

LOCAL_TOOLS = {
    "get_transaction_history": tools.get_transaction_history,
    "check_fraud_score": tools.check_fraud_score,
    "get_customer_profile": tools.get_customer_profile,
    "screen_customer": tools.screen_customer,
}


def call_tool(agent: str, tool: str, trace_id: str, **args) -> dict:
    if tool not in AGENT_TOOLS.get(agent, set()):
        log(trace_id, "gateway", "tool_denied", {"agent": agent, "tool": tool})
        raise PermissionError(f"{agent} is not allowed to use {tool}")

    args["trace_id"] = trace_id   # pass the trace ID along (Topic 11)

    if config.USE_MCP:
        url = config.MCP_URLS[TOOL_SERVER[tool]]
        return asyncio.run(_call_mcp(url, tool, args))
    return LOCAL_TOOLS[tool](**args)


async def _call_mcp(url: str, tool: str, args: dict) -> dict:
    """Call one tool on an MCP server (MCP client side)."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async with streamablehttp_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool, args)

    if result.isError:
        raise RuntimeError(f"MCP tool {tool} failed")

    data = getattr(result, "structuredContent", None)
    if not data:
        data = json.loads(result.content[0].text)
    # Some MCP versions wrap the answer as {"result": {...}}
    if isinstance(data, dict) and set(data) == {"result"} and isinstance(data["result"], dict):
        data = data["result"]
    return data
