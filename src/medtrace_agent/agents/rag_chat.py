"""Fast chat path: one LLM call with Zep memory context in the system prompt."""

from __future__ import annotations

import os
from typing import Sequence

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from zep_cloud.types import Message as ZepMessage

from medtrace_agent.fireworks_config import fireworks_chat_client


BASE_SYSTEM = """You are a concise, friendly assistant in a demo app backed by Zep long-term memory.
When "Memory context" includes facts about the user, rely on them and cite them implicitly when helpful.
If memory is empty or still updating, answer from the conversation messages and general knowledge."""

DOC_CATALOG_INSTRUCTIONS = """
The section "Ingested clinical documents" lists PDFs ingested in this app session (doc_id + filename).

Rules:
- When your answer uses facts that plausibly come from those PDFs or from Zep memory about this patient, name the source inline using **doc_id** and/or **filename** from that list.
- Always end your reply with a single line exactly in this form (pick one branch):
  - **Sources:** doc_id `YOUR_DOC_ID` — YOUR_FILENAME (repeat if multiple apply)
  - **Sources:** none — if you did not rely on any listed PDF or patient-specific memory for substantive claims.

Do not skip the **Sources:** line when "Ingested clinical documents" is present."""


def _zep_to_lc(messages: Sequence[ZepMessage]) -> list[BaseMessage]:
    out: list[BaseMessage] = []
    for m in messages:
        role = m.role
        name = m.name
        if role == "user":
            out.append(HumanMessage(content=m.content, name=name))
        elif role == "assistant":
            out.append(AIMessage(content=m.content, name=name))
    return out


def chat_with_memory(
    *,
    user_input: str,
    user_display_name: str | None,
    zep_context: str,
    thread_messages: Sequence[ZepMessage],
    model_name: str,
    temperature: float = 0.6,
    api_key: str | None = None,
    base_url: str | None = None,
    document_catalog: str | None = None,
) -> str:
    system_parts = [BASE_SYSTEM]
    ctx = (zep_context or "").strip()
    if ctx:
        system_parts.append("\n## Memory context (from Zep)\n")
        system_parts.append(ctx)

    cat = (document_catalog or "").strip()
    if cat:
        system_parts.append("\n## Ingested clinical documents\n")
        system_parts.append(cat)
        system_parts.append("\n")
        system_parts.append(DOC_CATALOG_INSTRUCTIONS)

    llm = fireworks_chat_client(
        model_name,
        temperature=temperature,
        api_key=api_key,
        base_url=base_url,
    )
    lc_messages: list[BaseMessage] = [SystemMessage(content="".join(system_parts))]
    lc_messages.extend(_zep_to_lc(thread_messages))
    lc_messages.append(
        HumanMessage(content=user_input, name=user_display_name or None)
    )

    response = llm.invoke(lc_messages)
    content = response.content
    if isinstance(content, str):
        return content
    return "".join(str(block.get("text", "")) for block in content)
