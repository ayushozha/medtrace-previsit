"""Chat thread (Zep + chat_sessions) routes."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

from fastapi import APIRouter, HTTPException, status
from langgraph.checkpoint.memory import MemorySaver

from apps.api.dependencies import RequireInsforgeDep
from apps.api.schemas import (
    ChatMessageOut,
    ChatThreadOut,
    CreateThreadIn,
    SendMessageIn,
    SendMessageOut,
)
from medtrace_agent.fireworks_config import fireworks_chat_model
from medtrace_agent.insforge_api import (
    fetch_documents_registry,
    find_chat_session_by_thread,
    get_chart_subject,
    list_chat_sessions_for_chart,
    touch_chat_session,
    upsert_chat_session_row,
)
from medtrace_agent.zep.memory import (
    append_turn,
    ensure_session,
    ensure_user,
    fetch_thread_context,
    get_zep_client,
)

router = APIRouter(tags=["threads"])


@lru_cache(maxsize=1)
def _shared_checkpointer() -> MemorySaver:
    """Process-wide MemorySaver (Deep Agent state lives in memory; clears on restart)."""
    return MemorySaver()


def _row_to_thread(row: dict[str, Any]) -> ChatThreadOut:
    return ChatThreadOut(
        id=str(row.get("id") or ""),
        zep_thread_id=str(row.get("zep_thread_id") or ""),
        title=row.get("title"),
        created_at=str(row.get("created_at") or ""),
        updated_at=str(row.get("updated_at") or row.get("created_at") or ""),
    )


def _format_document_catalog(docs: list[dict[str, Any]]) -> str:
    """Bulleted document catalog injected into the chat system prompt."""
    if not docs:
        return ""
    lines: list[str] = []
    for d in docs[:25]:
        did = d.get("doc_id") or "?"
        fname = d.get("filename") or "?"
        kind = d.get("document_kind") or "?"
        ec = d.get("episode_count") or 0
        lines.append(f"- doc_id `{did}` — {fname} ({kind}, {ec} episodes)")
    return "\n".join(lines)


@router.get(
    "/api/patients/{chart_subject_id}/threads",
    response_model=list[ChatThreadOut],
    dependencies=[RequireInsforgeDep],
)
def list_threads(chart_subject_id: str) -> list[ChatThreadOut]:
    if not get_chart_subject(chart_subject_id=chart_subject_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found.")
    rows = list_chat_sessions_for_chart(chart_subject_id=chart_subject_id)
    return [_row_to_thread(r) for r in rows]


@router.post(
    "/api/patients/{chart_subject_id}/threads",
    response_model=ChatThreadOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[RequireInsforgeDep],
)
def create_thread(chart_subject_id: str, body: CreateThreadIn) -> ChatThreadOut:
    chart = get_chart_subject(chart_subject_id=chart_subject_id)
    if not chart:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found.")
    zep_user_id = str(chart.get("zep_user_id") or "")
    if not zep_user_id:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="chart_subject is missing zep_user_id.",
        )
    zep_thread_id = f"react-{chart_subject_id[:8]}-{uuid.uuid4().hex[:10]}"
    # Charts seeded straight into the store (local mock, SQL seeds) have no Zep user yet,
    # and thread.create 404s without one. ensure_user is idempotent.
    ensure_user(zep_user_id, str(chart.get("display_name") or zep_user_id))
    ensure_session(zep_thread_id, zep_user_id)
    upsert_chat_session_row(
        zep_thread_id=zep_thread_id,
        chart_subject_id=chart_subject_id,
        title=body.title,
    )
    rows = list_chat_sessions_for_chart(chart_subject_id=chart_subject_id)
    for r in rows:
        if r.get("zep_thread_id") == zep_thread_id:
            return _row_to_thread(r)
    return ChatThreadOut(
        id="",
        zep_thread_id=zep_thread_id,
        title=body.title,
        created_at=datetime.now(timezone.utc).isoformat(),
        updated_at=datetime.now(timezone.utc).isoformat(),
    )


@router.get(
    "/api/threads/{zep_thread_id}/messages",
    response_model=list[ChatMessageOut],
    dependencies=[RequireInsforgeDep],
)
def list_messages(zep_thread_id: str, lastn: int = 50) -> list[ChatMessageOut]:
    client = get_zep_client()
    listed = client.thread.get(thread_id=zep_thread_id, lastn=max(1, min(int(lastn), 200)))
    msgs = list(listed.messages or [])
    msgs.sort(key=lambda m: m.created_at or "")
    out: list[ChatMessageOut] = []
    for m in msgs:
        out.append(
            ChatMessageOut(
                id=str(getattr(m, "uuid_", None) or getattr(m, "uuid", None) or uuid.uuid4().hex),
                role=("user" if m.role == "user" else "assistant"),
                content=str(m.content or ""),
                created_at=str(m.created_at) if m.created_at else None,
                name=m.name,
            )
        )
    return out


@router.post(
    "/api/threads/{zep_thread_id}/messages",
    response_model=SendMessageOut,
    dependencies=[RequireInsforgeDep],
)
def send_message(zep_thread_id: str, body: SendMessageIn) -> SendMessageOut:
    text = (body.user_input or "").strip()
    if not text:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="user_input is required.")

    chart_subject_id, chart = _resolve_chart_for_thread(zep_thread_id)
    zep_user_id = str(chart.get("zep_user_id") or "")
    display_name = str(chart.get("display_name") or "Patient")

    context, msgs = fetch_thread_context(zep_thread_id)
    docs = fetch_documents_registry(chart_subject_id=chart_subject_id) if chart_subject_id else []
    catalog = _format_document_catalog(docs)

    if body.deep:
        from medtrace_agent.agents.deep_clinical import run_clinical_deep_agent_turn

        assistant_text = run_clinical_deep_agent_turn(
            user_id=zep_user_id,
            thread_id=zep_thread_id,
            model_name=fireworks_chat_model(),
            user_input=text,
            document_catalog=catalog or None,
            checkpointer=_shared_checkpointer(),
        )
    else:
        from medtrace_agent.agents.rag_chat import chat_with_memory

        assistant_text = chat_with_memory(
            user_input=text,
            user_display_name=display_name,
            zep_context=context,
            thread_messages=msgs,
            model_name=fireworks_chat_model(),
            document_catalog=catalog or None,
        )

    append_turn(zep_thread_id, display_name, text, assistant_text)
    touch_chat_session(zep_thread_id=zep_thread_id)

    now_iso = datetime.now(timezone.utc).isoformat()
    return SendMessageOut(
        user=ChatMessageOut(
            id=uuid.uuid4().hex,
            role="user",
            content=text,
            created_at=now_iso,
            name=display_name,
        ),
        assistant=ChatMessageOut(
            id=uuid.uuid4().hex,
            role="assistant",
            content=assistant_text,
            created_at=now_iso,
            name="Assistant",
        ),
    )


def _resolve_chart_for_thread(zep_thread_id: str) -> tuple[str | None, dict[str, Any]]:
    """Find the chart_subject this Zep thread belongs to.

    Returns (chart_subject_id, chart_row). If no chat_sessions row exists, falls
    back to ``{}`` and lets downstream code handle missing display_name.
    """
    session = find_chat_session_by_thread(zep_thread_id=zep_thread_id) or {}
    cid = session.get("chart_subject_id")
    if not cid:
        return None, {}
    chart_subject_id = str(cid)
    chart = get_chart_subject(chart_subject_id=chart_subject_id) or {}
    return chart_subject_id, chart
