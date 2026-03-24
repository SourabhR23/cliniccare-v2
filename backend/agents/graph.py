"""
backend/agents/graph.py

IPv4 FIX:
  Supabase returns an IPv6 address via DNS on some networks.
  Windows often can't connect to PostgreSQL over IPv6 on port 5432.
  We resolve the hostname to IPv4 before connecting.

CHECKPOINTER STRATEGY:
  1. Try PostgresSaver with IPv4-resolved Supabase host
  2. Auto-fallback to MemorySaver if connection fails

  Startup log will show:
    [info]    agent_checkpointer  mode=postgres   ← Supabase working
    [warning] agent_checkpointer  mode=memory     ← using fallback
"""

import sys

if sys.platform == "win32":
    import asyncio
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import functools
import socket
import structlog
from urllib.parse import urlparse
from motor.motor_asyncio import AsyncIOMotorDatabase
from langgraph.graph import StateGraph, START, END

from backend.agents.state import AgentState
from backend.agents.supervisor import supervisor_node, route_to_agent, fallback_node
from backend.agents.receptionist_agent import (
    identify_patient, route_after_identify,
    fetch_patient_record, collect_info,
    validate_info, route_after_validate, register_patient,
)
from backend.agents.rag_agent import (
    think_and_act, route_after_think, run_tool, format_answer,
)
from backend.agents.scheduling_agent import (
    extract_appointment_details, check_slot_availability,
    route_after_availability, confirm_booking,
    send_reminder, wait_for_confirmation,
    classify_response, route_after_classification,
    send_confirmation_email, offer_alternatives,
    ask_clarification, notify_doctor_of_decline,
)
from backend.agents.notification_agent import (
    compose_email, send_email, route_after_send, log_result,
)
from backend.agents.calendar_agent import calendar_dispatch
from backend.tools.patient_tools import create_patient_tools
from backend.tools.rag_tools import create_rag_tools
from backend.core.config import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()


def _wrap(node_fn, **kwargs):
    @functools.wraps(node_fn)
    async def wrapped(state: AgentState) -> dict:
        return await node_fn(state, **kwargs)
    return wrapped


def _resolve_ipv4(hostname: str) -> str:
    """
    Resolve hostname to IPv4 address.
    Supabase DNS returns IPv6 on some networks (e.g. 2406:da1a:...).
    psycopg on Windows often cannot connect via IPv6 on port 5432.
    Using the IPv4 address directly bypasses this.
    """
    try:
        results = socket.getaddrinfo(hostname, 5432, socket.AF_INET)
        if results:
            ipv4 = results[0][4][0]
            logger.info("supabase_dns_resolved", hostname=hostname, ipv4=ipv4)
            return ipv4
    except Exception as e:
        logger.warning("supabase_dns_ipv4_failed", hostname=hostname, error=str(e))
    return hostname


async def _build_checkpointer():
    """
    Try PostgresSaver (Supabase) first, auto-fallback to MemorySaver.
    """
    supabase_url = settings.supabase_db_url
    placeholder_signals = ["://...", "://…", "<password>", "DATABASE_URL="]
    is_placeholder = not supabase_url or any(s in supabase_url for s in placeholder_signals)

    if not is_placeholder:
        try:
            import psycopg
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

            parsed = urlparse(supabase_url)
            ipv4_host = _resolve_ipv4(parsed.hostname)

            conn = await psycopg.AsyncConnection.connect(
                host=ipv4_host,
                port=parsed.port or 5432,
                dbname=parsed.path.lstrip("/"),
                user=parsed.username,
                password=parsed.password,
                autocommit=True,
                connect_timeout=15,
            )
            checkpointer = AsyncPostgresSaver(conn)
            await checkpointer.setup()
            logger.info("agent_checkpointer", mode="postgres",
                        host=ipv4_host)
            return checkpointer, "postgres"

        except Exception as e:
            logger.warning(
                "agent_checkpointer_postgres_failed",
                error=str(e),
                fallback="MemorySaver",
            )

    from langgraph.checkpoint.memory import MemorySaver
    logger.warning(
        "agent_checkpointer",
        mode="memory",
        detail="State lost on restart. Fix SUPABASE_DB_URL for persistence.",
    )
    return MemorySaver(), "memory"


async def build_graph(db: AsyncIOMotorDatabase, redis_client=None):
    patient_tools = create_patient_tools(db)
    rag_tools = create_rag_tools(db, redis_client)

    logger.info("building_agent_graph")
    graph = StateGraph(AgentState)

    # Supervisor
    graph.add_node("supervisor", supervisor_node)
    graph.add_node("fallback",   fallback_node)

    # Receptionist
    graph.add_node("identify_patient",     _wrap(identify_patient,      tools=patient_tools))
    graph.add_node("fetch_patient_record", _wrap(fetch_patient_record,   tools=patient_tools))
    graph.add_node("collect_info",         _wrap(collect_info,           tools=patient_tools))
    graph.add_node("validate_info",        _wrap(validate_info,          tools=patient_tools))
    graph.add_node("register_patient",     _wrap(register_patient,       tools=patient_tools))

    # RAG
    graph.add_node("think_and_act", _wrap(think_and_act, tools=rag_tools))
    graph.add_node("run_tool",      _wrap(run_tool,      tools=rag_tools))
    graph.add_node("format_answer", format_answer)

    # Scheduling
    graph.add_node("extract_appointment_details", _wrap(extract_appointment_details, db=db))
    graph.add_node("check_slot_availability",     _wrap(check_slot_availability, db=db))
    graph.add_node("confirm_booking",             _wrap(confirm_booking,         db=db))
    graph.add_node("send_reminder",               send_reminder)
    graph.add_node("wait_for_confirmation",       wait_for_confirmation)
    graph.add_node("classify_response",           classify_response)
    graph.add_node("send_confirmation_email",     _wrap(send_confirmation_email, db=db))
    graph.add_node("offer_alternatives",          offer_alternatives)
    graph.add_node("ask_clarification",           ask_clarification)
    graph.add_node("notify_doctor_of_decline",    _wrap(notify_doctor_of_decline, db=db))

    # Notification
    graph.add_node("compose_email", compose_email)
    graph.add_node("send_email",    send_email)
    graph.add_node("log_result",    log_result)

    # Calendar
    graph.add_node("calendar_agent", _wrap(calendar_dispatch, db=db))

    # Edges
    graph.add_edge(START, "supervisor")
    graph.add_conditional_edges("supervisor", route_to_agent, {
        "receptionist_agent": "identify_patient",
        "rag_agent":          "think_and_act",
        "scheduling_agent":   "extract_appointment_details",
        "notification_agent": "compose_email",
        "calendar_agent":     "calendar_agent",
        "fallback":           "fallback",
    })
    graph.add_edge("fallback", END)

    graph.add_conditional_edges("identify_patient", route_after_identify, {
        "fetch_patient_record": "fetch_patient_record",
        "collect_info":         "collect_info",
    })
    graph.add_edge("fetch_patient_record", END)
    graph.add_edge("collect_info", "validate_info")
    graph.add_conditional_edges("validate_info", route_after_validate, {
        "register_patient": "register_patient",
        "collect_info":     "collect_info",
        "__end__":          END,
    })
    graph.add_edge("register_patient", END)

    graph.add_conditional_edges("think_and_act", route_after_think, {
        "run_tool":      "run_tool",
        "format_answer": "format_answer",
    })
    graph.add_edge("run_tool", "think_and_act")
    graph.add_edge("format_answer", END)

    graph.add_conditional_edges("extract_appointment_details",
        lambda s: "abort" if s.get("intent") in ("abort", "slot_selection") else "continue",
        {"abort": END, "continue": "check_slot_availability"})
    graph.add_conditional_edges("check_slot_availability", route_after_availability,
        {"confirm_booking": "confirm_booking", "__end__": END})
    graph.add_edge("confirm_booking",       "send_reminder")
    graph.add_edge("send_reminder",         "wait_for_confirmation")
    graph.add_edge("wait_for_confirmation", "classify_response")
    graph.add_conditional_edges("classify_response", route_after_classification, {
        "send_confirmation_email":  "send_confirmation_email",
        "offer_alternatives":       "offer_alternatives",
        "ask_clarification":        "ask_clarification",
        "notify_doctor_of_decline": "notify_doctor_of_decline",
    })
    graph.add_edge("send_confirmation_email",  END)
    graph.add_edge("offer_alternatives",       "classify_response")
    graph.add_edge("ask_clarification",        "classify_response")
    graph.add_edge("notify_doctor_of_decline", END)

    graph.add_edge("compose_email", "send_email")
    graph.add_conditional_edges("send_email", route_after_send,
        {"send_email": "send_email", "log_result": "log_result"})
    graph.add_edge("log_result", END)

    graph.add_edge("calendar_agent", END)

    checkpointer, mode = await _build_checkpointer()
    compiled = graph.compile(checkpointer=checkpointer)
    logger.info("agent_graph_compiled", checkpointer_mode=mode)
    return compiled