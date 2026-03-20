"""
backend/core/llm.py

EURI API COMPATIBILITY FIX
==========================
LangChain 1.x converts max_tokens → max_completion_tokens in the API request.
EURI uses the old OpenAI format (max_tokens), ignores max_completion_tokens,
and defaults max_tokens to 100000 when it is absent — then rejects it (>16384).

This module provides:
  - _EURIFixTransport: httpx transport that rewrites the request body,
    converting max_completion_tokens → max_tokens before the request hits EURI.
  - make_chat_llm(): factory that returns a properly configured ChatOpenAI,
    with the fix applied when OPENAI_BASE_URL (EURI) is set.

Usage in agents:
    from backend.core.llm import make_chat_llm
    _llm = make_chat_llm(temperature=0)
"""

import json
import httpx
from openai import AsyncOpenAI
from langchain_openai import ChatOpenAI
from backend.core.config import get_settings


class _EURIFixTransport(httpx.AsyncBaseTransport):
    """
    Intercepts outbound OpenAI-format requests and converts:
      max_completion_tokens → max_tokens

    Also injects a safe default (4096) when neither is present,
    preventing EURI from defaulting to 100000.
    """

    def __init__(self, wrapped: httpx.AsyncBaseTransport):
        self._wrapped = wrapped

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        try:
            body = json.loads(request.content)
            if "max_completion_tokens" in body:
                body["max_tokens"] = body.pop("max_completion_tokens")
            elif "max_tokens" not in body:
                body["max_tokens"] = 4096

            new_content = json.dumps(body).encode()

            # Rebuild headers, recalculating Content-Length for the new body
            headers = [
                (k, v) for k, v in request.headers.raw
                if k.lower() != b"content-length"
            ]
            headers.append((b"content-length", str(len(new_content)).encode()))

            request = httpx.Request(
                method=request.method,
                url=request.url,
                headers=headers,
                content=new_content,
            )
        except Exception:
            pass  # Leave request unchanged if body is not JSON

        return await self._wrapped.handle_async_request(request)


def make_chat_llm(temperature: float = 0, max_tokens: int = 4096) -> ChatOpenAI:
    """
    Factory: returns a ChatOpenAI configured for EURI or real OpenAI.

    - EURI (OPENAI_BASE_URL set): uses _EURIFixTransport to ensure max_tokens
      is sent correctly.
    - Real OpenAI (no base URL): uses standard ChatOpenAI with max_tokens.
    """
    settings = get_settings()

    if settings.openai_base_url:
        transport = _EURIFixTransport(httpx.AsyncHTTPTransport())
        async_openai = AsyncOpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            http_client=httpx.AsyncClient(transport=transport),
        )
        return ChatOpenAI(
            model=settings.openai_chat_model,
            temperature=temperature,
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            async_client=async_openai.chat.completions,
        )

    # Standard OpenAI — max_completion_tokens is natively supported
    return ChatOpenAI(
        model=settings.openai_chat_model,
        temperature=temperature,
        api_key=settings.openai_api_key,
        max_tokens=max_tokens,
    )
