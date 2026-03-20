"""
backend/agents/rag_agent.py — ReAct loop for clinical queries
"""

import json
import asyncio
import structlog
from langchain_core.messages import SystemMessage, AIMessage, ToolMessage

from backend.agents.state import AgentState
from backend.core.config import get_settings
from backend.core.llm import make_chat_llm

logger = structlog.get_logger(__name__)
settings = get_settings()

_llm = make_chat_llm(temperature=0.1)

RAG_AGENT_SYSTEM = """You are a clinical assistant helping doctors access patient medical history.

Available tools:
  - lookup_patient_by_name(name): Resolves a patient name to their real patient_id.
  - rag_query(query, patient_id): Searches visit records semantically + by keywords.
  - previsit_brief(patient_id): Generates a structured pre-visit clinical summary.

CRITICAL WORKFLOW — follow this order strictly:
  1. If patient_id in context is "unknown" AND the user mentions a patient name:
     → Call lookup_patient_by_name(name) FIRST to get the real patient_id.
     → Then call rag_query or previsit_brief with that patient_id.
     → NEVER guess or invent a patient_id — always use lookup_patient_by_name.
  2. If patient_id is already known (not "unknown"):
     → Call rag_query or previsit_brief directly.
  3. If lookup_patient_by_name returns found=False:
     → Report that the patient was not found — do NOT call rag_query.

RULES:
  1. NEVER invent or guess a patient_id — always obtain it via lookup_patient_by_name
  2. NEVER add clinical knowledge not present in the tool results
  3. If records don't contain the answer, say so clearly — do NOT guess
  4. Use clinical terminology appropriate for a medical audience
  5. If the answer is in the tool results, do NOT call more tools — answer directly

Patient context: {patient_id}"""

MAX_TOOL_CALLS = 5


async def think_and_act(state: AgentState, tools: list) -> dict:
    if state.get("tool_calls_made", 0) >= MAX_TOOL_CALLS:
        logger.warning("rag_agent_loop_guard_triggered",
                       tool_calls_made=state.get("tool_calls_made"))
        return {"intent": "force_final_answer"}

    patient_id = state.get("patient_id", "unknown")
    llm_with_tools = _llm.bind_tools(tools)

    response = await llm_with_tools.ainvoke([
        SystemMessage(content=RAG_AGENT_SYSTEM.format(patient_id=patient_id)),
        *state["messages"],
    ])

    if response.tool_calls:
        logger.info("rag_agent_tool_call",
                    tools=[c["name"] for c in response.tool_calls])
        return {"messages": [response], "intent": "tool_call"}

    return {
        "messages": [response],
        "intent": "final_answer",
        "rag_answer": response.content,
    }


def route_after_think(state: AgentState) -> str:
    return "format_answer" if state.get("intent") in ("final_answer", "force_final_answer") else "run_tool"


async def run_tool(state: AgentState, tools: list) -> dict:
    last_message = state["messages"][-1]
    tool_calls = getattr(last_message, "tool_calls", [])
    if not tool_calls:
        return {"intent": "final_answer"}

    tool_map = {t.name: t for t in tools}

    async def dispatch(call):
        tool_fn = tool_map.get(call["name"])
        if not tool_fn:
            return ToolMessage(
                content=json.dumps({"error": f"Tool {call['name']} not found"}),
                tool_call_id=call["id"],
            )
        try:
            result = await tool_fn.ainvoke(call["args"])
            return ToolMessage(
                content=json.dumps(result) if not isinstance(result, str) else result,
                tool_call_id=call["id"],
            )
        except Exception as e:
            logger.error("rag_tool_error", tool=call["name"], error=str(e))
            return ToolMessage(
                content=json.dumps({"error": str(e)}),
                tool_call_id=call["id"],
            )

    tool_results = await asyncio.gather(*[dispatch(c) for c in tool_calls])

    rag_sources = list(state.get("rag_sources", []))
    for tr in tool_results:
        try:
            data = json.loads(tr.content)
            if "sources" in data:
                rag_sources.extend(data["sources"])
        except Exception:
            pass

    return {
        "messages": list(tool_results),
        "tool_calls_made": state.get("tool_calls_made", 0) + len(tool_calls),
        "rag_sources": rag_sources,
        "intent": "continue",
    }


async def format_answer(state: AgentState) -> dict:
    intent = state.get("intent", "")
    sources = state.get("rag_sources", [])

    if intent == "force_final_answer":
        tool_data = []
        for msg in state["messages"]:
            if hasattr(msg, "type") and msg.type == "tool":
                try:
                    data = json.loads(msg.content)
                    if "answer" in data:
                        tool_data.append(data["answer"])
                except Exception:
                    pass
        answer = ("Based on available records:\n\n" + "\n\n".join(tool_data)
                  if tool_data else "Unable to find sufficient information to answer this question.")
        return {"messages": [AIMessage(content=answer)], "rag_answer": answer}

    if sources:
        source_ids = list(set(s.get("visit_id", "") for s in sources if s.get("visit_id")))
        if source_ids:
            last_content = state["messages"][-1].content
            if not any(sid in last_content for sid in source_ids):
                citation = f"\n\n*Sources: {', '.join(source_ids[:4])}*"
                return {"messages": [AIMessage(content=last_content + citation)]}

    return {}