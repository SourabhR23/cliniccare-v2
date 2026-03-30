"""
backend/agents/history_compressor.py

Compresses LangGraph conversation history when thread length exceeds a threshold.
Called from supervisor_node() at the top of every message — before routing.

WHY:
  LangGraph checkpointer saves full message history. A 25-turn receptionist
  session sends all prior turns on every new message, ballooning token cost
  by 5–8× compared to a fresh session.

HOW:
  When len(messages) > COMPRESSION_THRESHOLD:
    1. Take the oldest COMPRESS_WINDOW messages
    2. Call LLM to summarise them into concise bullet points
    3. Replace those messages with a single SystemMessage("[Session summary]...")
    4. Keep the most recent KEEP_RECENT messages verbatim

WHAT IS PRESERVED:
  - Patient names + IDs
  - Appointment IDs created / cancelled
  - Decisions made (registered, booked, cancelled, sent email)
  - Key dates and doctor assignments

WHAT IS DISCARDED (acceptable):
  - Exact wording of prior bot replies
  - Conversational filler ("Sure!", "Got it", "Let me check...")
  - Repeated questions and intermediate reasoning

FAILURE SAFETY:
  If the LLM summarisation call fails, maybe_compress() catches the exception
  and returns the original messages unchanged. The session continues normally.
  Compression is never a blocking dependency.

TRIGGER POINT:
  supervisor_node() — entry point for every staff message. Compression resolves
  before any routing logic runs, so the rest of the graph sees the compacted state.
"""

import structlog
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, BaseMessage

from backend.core.llm import make_chat_llm

logger = structlog.get_logger(__name__)

# ── Thresholds ────────────────────────────────────────────────────────────────
COMPRESSION_THRESHOLD = 12   # Compress when history exceeds this many messages
COMPRESS_WINDOW       = 8    # How many of the oldest messages to summarise
KEEP_RECENT           = 4    # Most recent messages always kept verbatim

# Cheapest available model is fine for summarisation — just bullet points
_compressor_llm = make_chat_llm(temperature=0)

_COMPRESS_PROMPT = """\
Summarise the following clinic staff chat session into concise bullet points.

Focus on: patient names/IDs, appointment IDs, decisions made, dates, doctor assignments.
Discard:  greetings, filler phrases, repeated questions, apologies.
Maximum 6 bullet points. Output ONLY the bullet list — no preamble, no explanation.

Conversation:
{conversation}"""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _should_compress(messages: list[BaseMessage]) -> bool:
    return len(messages) > COMPRESSION_THRESHOLD


def _format_for_compression(messages: list[BaseMessage]) -> str:
    """Convert messages to plain text. Skip UI payload lines."""
    lines: list[str] = []
    for msg in messages:
        content = msg.content or ""
        # Skip structured UI payloads — not human-readable context
        if content.startswith("__"):
            continue
        if isinstance(msg, HumanMessage):
            lines.append(f"Staff: {content[:200]}")
        elif isinstance(msg, AIMessage):
            lines.append(f"Assistant: {content[:200]}")
        # SystemMessages (e.g. prior summaries) are intentionally excluded —
        # we don't want to re-summarise a summary.
    return "\n".join(lines)


# ── Public API ────────────────────────────────────────────────────────────────

async def maybe_compress(messages: list[BaseMessage]) -> list[BaseMessage]:
    """
    Compress conversation history if above threshold.

    Returns the (possibly compressed) messages list.
    If compression is not needed or fails, returns the original list unchanged.

    Compressed result shape:
        [SystemMessage("Session summary: ...")]   ← replaces oldest messages
        + last KEEP_RECENT messages verbatim
    """
    if not _should_compress(messages):
        return messages

    to_compress = messages[:-KEEP_RECENT]   # oldest batch
    to_keep     = messages[-KEEP_RECENT:]   # most recent, always verbatim

    conversation_text = _format_for_compression(to_compress)
    if not conversation_text.strip():
        # Nothing useful to compress (all UI payloads) — skip
        return messages

    try:
        prompt   = _COMPRESS_PROMPT.format(conversation=conversation_text)
        response = await _compressor_llm.ainvoke([HumanMessage(content=prompt)])
        summary  = response.content.strip()

        logger.info(
            "history_compressed",
            original_count=len(messages),
            compressed_to=len(to_keep) + 1,
            summary_preview=summary[:120],
        )

        summary_msg = SystemMessage(
            content=f"[Session summary — earlier conversation compressed]\n{summary}"
        )
        return [summary_msg] + list(to_keep)

    except Exception as exc:
        logger.warning("history_compression_failed", error=str(exc))
        return messages  # Safe fallback: original list, session unaffected
