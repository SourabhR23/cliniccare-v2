"""
backend/rag/complexity_classifier.py

Classifies a doctor's RAG query as "simple" or "complex" using fast
regex/keyword matching. Zero LLM calls — runs in microseconds.

SIMPLE  → single fact lookup, most-recent value, current status
          Optimised path: top-4 retrieval, skip reranker, max_tokens=300
          Avg cost: ~1,800 tokens

COMPLEX → trend analysis, multi-condition, time-range comparison, causal reasoning
          Full path: top-10 retrieval, reranker, max_tokens=600
          Avg cost: ~5,500 tokens

PRIORITY RULE:
  Complex patterns are checked first and always win.
  A query matching both simple and complex signals is routed as complex.

EXAMPLES:
  "What was the last prescribed medication?"          → simple
  "Any allergies on file?"                            → simple
  "How has HbA1c changed since we increased insulin?" → complex
  "Compare BP readings over the last 6 months"        → complex
  "summarize history"                                 → complex
"""

import re
from typing import Literal

QueryComplexity = Literal["simple", "complex"]


# ── Complex patterns ──────────────────────────────────────────────────────────
# Signals requiring multi-record reasoning, time-range analysis, or causal logic.
_COMPLEX_PATTERNS: list[str] = [
    r"\bover\s+(the\s+)?(last|past)\s+\d+\s+(day|week|month|year)s?\b",
    r"\btrend\b",
    r"\bcompare\b",
    r"\bcorrelat\w*\b",
    r"\bprogress(ion)?\b",
    r"\bchange\w*\b.{0,30}\b(since|after|when|following)\b",
    r"\b(worse|better|improv\w+|deteriorat\w+)\b",
    r"\bsince\b.{0,40}\b(start|began|changed|increased|decreased|adjusted)\b",
    r"\brelat\w+\s+to\b",
    r"\beffect\s+of\b",
    r"\bbecause\s+of\b",
    r"\b(increase|decrease|reduc|adjust)\w*\s+in\s+(dose|dosage|medication|insulin|treatment)\b",
    r"\ball\s+visits\b",
    r"\boverview\b",
    r"\bpattern\b",
    r"\brecurr\w*\b",
    r"\bsummar\w+\b.{0,20}\b(history|record|visit|all)\b",
    r"\bhistory\s+of\b.{0,30}\band\b",   # "history of X and Y" — multiple conditions
    r"\bchronic\b.{0,40}\bchronic\b",    # two chronic conditions mentioned
    r"\bthroughout\b",
    r"\blong[\s-]term\b",
    r"\bpre[\s-]?visit\s+brief\b",       # always full pipeline
]

# ── Simple patterns ───────────────────────────────────────────────────────────
# Signals for single-record, single-fact, or current-status lookups.
_SIMPLE_PATTERNS: list[str] = [
    r"\b(last|latest|most\s+recent|previous)\s+(medication|drug|prescription|dose)\b",
    r"\b(last|latest|most\s+recent)\s+(diagnosis|complaint|visit|appointment|result|reading)\b",
    r"\bcurrent\s+(medication|condition|status|treatment|prescription)\b",
    r"\bwhat\s+(was|is|were)\b.{0,40}\b(prescribed|diagnosed|noted|recorded|found)\b",
    r"\bwhen\s+(was|did)\b.{0,30}\b(last|visit|come)\b",
    r"\bany\s+(allerg\w+)\b",
    r"\ballerg\w+\s+on\s+file\b",
    r"\bblood\s+(group|type)\b",
    r"\bhow\s+many\s+visits\b",
    r"\bnumber\s+of\s+visits\b",
    r"\bage\b",
    r"\bdate\s+of\s+birth\b",
    r"\blast\s+bp\b",
    r"\blast\s+blood\s+pressure\b",
    r"\blast\s+sugar\b",
    r"\blast\s+weight\b",
    r"\bphone\b.{0,10}\bpatient\b",
    r"\bemail\b.{0,10}\bpatient\b",
]

# Queries with fewer than this many words are treated as simple unless
# they explicitly match a complex pattern.
_SHORT_QUERY_WORD_THRESHOLD = 7


def classify(query: str) -> QueryComplexity:
    """
    Classify a RAG query as 'simple' or 'complex'.

    Complex patterns take priority — checked first.
    If no pattern matches, short queries default to simple,
    longer queries default to complex (conservative).
    """
    lower = query.lower().strip()

    # Complex takes precedence — check first
    for pattern in _COMPLEX_PATTERNS:
        if re.search(pattern, lower):
            return "complex"

    # Check simple patterns
    for pattern in _SIMPLE_PATTERNS:
        if re.search(pattern, lower):
            return "simple"

    # Unclassified: short query → simple, long query → complex (conservative)
    word_count = len(lower.split())
    return "simple" if word_count < _SHORT_QUERY_WORD_THRESHOLD else "complex"
