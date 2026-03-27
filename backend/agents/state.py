"""
backend/agents/state.py

SHARED AGENT STATE — Single TypedDict used by ALL agents.

WHY A SINGLE STATE:
  All agents share one graph. The supervisor routes to sub-graphs.
  A unified state means any agent can read context set by any other.
  For example: receptionist sets patient_id, then scheduling_agent
  reads it without any hand-off boilerplate.

REDUCERS:
  The `messages` field uses add_messages reducer — meaning LangGraph
  APPENDS new messages rather than replacing the list. Every other
  field uses last-write-wins (standard dict update).

  add_messages is critical because:
  - Multiple agents write messages across the conversation
  - We never want to lose message history
  - The LLM needs the full conversation to maintain context

STATE PERSISTENCE (PostgresSaver):
  After every node, LangGraph serialises this entire dict to Postgres.
  All fields must be JSON-serialisable. No Python objects, no datetimes
  that aren't ISO strings, no custom classes.
"""

from typing import Annotated, Optional, List
from typing_extensions import TypedDict
from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    # ── Conversation history ─────────────────────────────────
    # add_messages reducer: new messages APPEND, never overwrite.
    # BaseMessage subclasses: HumanMessage, AIMessage, ToolMessage, SystemMessage
    messages: Annotated[list, add_messages]

    # ── Routing & Control ────────────────────────────────────
    current_agent: str              # Which agent is active: RECEPTIONIST | RAG_AGENT | SCHEDULING | NOTIFICATION | UNKNOWN
    intent: str                     # Classified intent from supervisor
    confidence: float               # 0.0–1.0. Below 0.70 → fallback
    fallback_reason: Optional[str]  # Why fallback triggered: low_confidence | tool_error | llm_timeout | parse_error

    # ── Staff context ────────────────────────────────────────
    staff_id: str                   # User ID from JWT (receptionist or admin)
    staff_name: str                 # Human-readable name for email composition
    staff_role: str                 # receptionist | admin

    # ── Patient context ──────────────────────────────────────
    patient_id: Optional[str]       # MongoDB _id e.g. PT92D3B32E
    patient_name: Optional[str]
    patient_email: Optional[str]    # For notification emails
    patient_phone: Optional[str]
    is_new_patient: Optional[bool]  # Controls receptionist branch
    assigned_doctor_id: Optional[str]
    assigned_doctor_name: Optional[str]
    pending_followup_date: Optional[str]  # ISO date — for email content context

    # ── Registration context (receptionist agent) ────────────
    collected_fields: Optional[dict]   # Partially collected patient info
    registration_attempts: int         # How many validation failures so far

    # ── RAG context ──────────────────────────────────────────
    rag_query: Optional[str]
    rag_answer: Optional[str]
    rag_sources: List[dict]            # [{"visit_id": ..., "visit_date": ..., "diagnosis": ...}]
    tool_calls_made: int               # Loop guard: max 5 tool calls in ReAct

    # ── Scheduling context ───────────────────────────────────
    appointment_date: Optional[str]    # ISO date string: "2026-04-15"
    appointment_slot: Optional[str]    # "10:30 AM"
    followup_reason: Optional[str]     # From visit document
    confirmation_status: Optional[str] # confirmed | declined | unclear | timeout
    reminder_sent: bool                # Idempotency guard
    scheduling_retry_count: int        # Max 3 reschedule attempts
    booking_done: Optional[bool]       # True after a booking is confirmed in this session

    # ── Notification context ─────────────────────────────────
    email_type: Optional[str]          # reminder | confirmation | cancellation | alert
    email_body: Optional[str]          # Composed by LLM
    email_sent: bool
    email_attempt: int                 # Retry counter (max 3)
    notification_thread_id: Optional[str]  # Links back to scheduling thread

    # ── Drug checker context ─────────────────────────────────
    medications_to_check: List[str]    # Drug names from new visit
    drug_alerts: List[dict]            # [{"drugs": [...], "interaction": "..."}]

    # ── Error tracking ───────────────────────────────────────
    error: Optional[str]               # Last error — never shown to user, used for routing
    error_count: int                   # How many consecutive errors

    # ── Thread metadata ──────────────────────────────────────
    thread_id: str                     # UUID — PostgresSaver key, returned in API response
