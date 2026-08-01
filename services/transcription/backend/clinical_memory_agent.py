"""
Clinical memory agent — Zep/FHIR chart Q&A via the MedTrace FastAPI.

Answers doctor questions from the canonical patient chart (apps/api threads +
rag_chat). Does not update dashboard UI widgets.
"""

from __future__ import annotations

import os
import uuid
from typing import Any, List, Optional

import httpx
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.types import Command

# patient_id -> zep_thread_id for Copilot chart memory threads
_THREAD_CACHE: dict[str, str] = {}


class ClinicalMemoryState(MessagesState):
    patient_id: Optional[str] = None
    patient_context: Optional[str] = None
    checklist: Optional[List[dict[str, Any]]] = None
    insights: Optional[List[dict[str, Any]]] = None
    focus: Optional[dict[str, Any]] = None
    tools: List[Any]


def _api_base() -> str:
    return (os.environ.get("MEDTRACE_API_URL") or "http://127.0.0.1:8001").rstrip("/")


def _last_user_text(messages: list[Any]) -> str:
    for msg in reversed(messages or []):
        if isinstance(msg, HumanMessage):
            content = msg.content
            return content if isinstance(content, str) else str(content)
        if isinstance(msg, dict) and msg.get("role") == "user":
            return str(msg.get("content") or "")
        role = getattr(msg, "type", None) or getattr(msg, "role", None)
        if role in {"human", "user"}:
            content = getattr(msg, "content", "")
            return content if isinstance(content, str) else str(content)
    return ""


async def _ensure_thread(client: httpx.AsyncClient, patient_id: str) -> str:
    cached = _THREAD_CACHE.get(patient_id)
    if cached:
        return cached
    list_resp = await client.get(f"{_api_base()}/api/patients/{patient_id}/threads")
    list_resp.raise_for_status()
    threads = list_resp.json()
    if isinstance(threads, list) and threads:
        zep_thread_id = str(threads[0].get("zep_thread_id") or "")
        if zep_thread_id:
            _THREAD_CACHE[patient_id] = zep_thread_id
            return zep_thread_id
    create_resp = await client.post(
        f"{_api_base()}/api/patients/{patient_id}/threads",
        json={"title": "Copilot chart memory"},
    )
    create_resp.raise_for_status()
    created = create_resp.json()
    zep_thread_id = str(created.get("zep_thread_id") or "")
    if not zep_thread_id:
        raise RuntimeError("API created a thread without zep_thread_id.")
    _THREAD_CACHE[patient_id] = zep_thread_id
    return zep_thread_id


async def _ask_api(patient_id: str, user_text: str, *, deep: bool = False) -> str:
    async with httpx.AsyncClient(timeout=120.0) as client:
        zep_thread_id = await _ensure_thread(client, patient_id)
        resp = await client.post(
            f"{_api_base()}/api/threads/{zep_thread_id}/messages",
            json={
                "user_input": user_text,
                "deep": deep,
                "request_id": uuid.uuid4().hex,
            },
        )
        resp.raise_for_status()
        payload = resp.json()
    assistant = payload.get("assistant") or {}
    content = assistant.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()
    return "I could not retrieve a chart answer. Confirm the API is running and Medplum/Zep are configured."


async def start_node(state: ClinicalMemoryState, config: RunnableConfig):  # pylint: unused-argument
    return Command(goto="chat_node")


async def chat_node(state: ClinicalMemoryState, config: Optional[RunnableConfig] = None):
    patient_id = (state.get("patient_id") or "").strip()
    user_text = _last_user_text(state.get("messages") or []).strip()
    patient_context = state.get("patient_context") or ""

    if not patient_id:
        reply = (
            "No patient_id is in agent state, so I cannot query clinical memory. "
            "Open a patient chart and try again."
        )
        return Command(
            goto=END,
            update={"messages": state["messages"] + [AIMessage(id=str(uuid.uuid4()), content=reply)]},
        )

    if not user_text:
        reply = "Send a clinical question about this patient chart."
        return Command(
            goto=END,
            update={"messages": state["messages"] + [AIMessage(id=str(uuid.uuid4()), content=reply)]},
        )

    # Prefer API (Zep + FHIR). If it fails, fall back to the snapshot context already
    # seeded into agent state so the chat still answers offline from the chart JSON.
    try:
        answer = await _ask_api(patient_id, user_text, deep=False)
    except Exception as exc:  # noqa: BLE001 — surface a usable CDS reply
        if patient_context.strip():
            from langchain_openai import ChatOpenAI

            model = ChatOpenAI(
                model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
                base_url=os.environ.get("OPENAI_BASE_URL") or None,
                api_key=os.environ.get("OPENAI_API_KEY") or None,
            )
            response = await model.ainvoke(
                [
                    SystemMessage(
                        content=(
                            "You are a non-diagnostic clinical decision-support assistant. "
                            "Answer only from the patient context below. Cite uncertainty. "
                            "Do not claim a diagnosis.\n\n"
                            f"Patient context:\n{patient_context}\n\n"
                            f"(Note: live memory API unavailable: {exc})"
                        )
                    ),
                    HumanMessage(content=user_text),
                ],
                config or RunnableConfig(recursion_limit=10),
            )
            answer = (
                response.content
                if isinstance(response.content, str)
                else str(response.content)
            )
        else:
            answer = (
                f"Clinical memory is unavailable ({exc}). "
                "Start the MedTrace API (`npm run dev:api`) with Medplum configured."
            )

    return Command(
        goto=END,
        update={
            "messages": state["messages"]
            + [AIMessage(id=str(uuid.uuid4()), content=answer)],
        },
    )


workflow = StateGraph(ClinicalMemoryState)
workflow.add_node("start_node", start_node)
workflow.add_node("chat_node", chat_node)
workflow.set_entry_point("start_node")
workflow.add_edge(START, "start_node")
workflow.add_edge("start_node", "chat_node")
workflow.add_edge("chat_node", END)

memory = MemorySaver()
clinical_memory_graph = workflow.compile(checkpointer=memory)
