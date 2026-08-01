"""Zep Cloud helpers: user, thread (session), context retrieval, and message ingestion."""

from __future__ import annotations

import os
from functools import lru_cache

from zep_cloud import Zep
from zep_cloud.errors import BadRequestError, ConflictError
from zep_cloud.types import Message


def _api_message(exc: BadRequestError) -> str:
    body = exc.body
    if isinstance(body, dict):
        return str(body.get("message") or "").lower()
    return str(body or "").lower()


def _split_name(display_name: str) -> tuple[str, str]:
    parts = (display_name or "User").strip().split(None, 1)
    first = parts[0] or "User"
    last = parts[1] if len(parts) > 1 else "Demo"
    return first, last


@lru_cache(maxsize=1)
def get_zep_client() -> Zep:
    key = os.environ.get("ZEP_API_KEY")
    if not key:
        raise RuntimeError("ZEP_API_KEY is not set.")
    return Zep(api_key=key)


def ensure_user(user_id: str, display_name: str, email: str | None = None) -> None:
    client = get_zep_client()
    first, last = _split_name(display_name)
    try:
        client.user.add(
            user_id=user_id,
            first_name=first,
            last_name=last,
            email=email or f"{user_id}@demo.local",
        )
    except ConflictError:
        pass
    except BadRequestError as e:
        msg = _api_message(e)
        if "user already exists" in msg or "already exists with user_id" in msg:
            return
        raise


def ensure_session(session_id: str, user_id: str) -> None:
    client = get_zep_client()
    try:
        client.thread.create(thread_id=session_id, user_id=user_id)
    except ConflictError:
        pass
    except BadRequestError as e:
        msg = _api_message(e)
        # Zep may say "thread" or "session" for the same resource id.
        if (
            "thread already exists" in msg
            or "already exists with thread" in msg
            or ("session" in msg and "already exists" in msg)
        ):
            return
        raise


def fetch_thread_context(session_id: str) -> tuple[str, list[Message]]:
    """Returns Zep context string and recent thread messages (short-term memory tail)."""
    client = get_zep_client()
    ctx_resp = client.thread.get_user_context(thread_id=session_id)
    context = (ctx_resp.context or "").strip()
    listed = client.thread.get(thread_id=session_id, lastn=8)
    messages = list(listed.messages or [])
    messages.sort(key=lambda m: m.created_at or "")
    return context, messages


def append_turn(
    session_id: str,
    user_display_name: str,
    user_text: str,
    assistant_text: str,
) -> None:
    client = get_zep_client()
    messages = [
        Message(role="user", name=user_display_name, content=user_text),
        Message(role="assistant", name="Assistant", content=assistant_text),
    ]
    client.thread.add_messages(session_id, messages=messages)
