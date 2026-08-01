"""Unit tests for ``medtrace_agent.zep.memory`` (mocked Zep client)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from zep_cloud.errors import BadRequestError, ConflictError
from zep_cloud.types import Message
from zep_cloud.types.api_error import ApiError

from medtrace_agent.zep import memory


def test_get_zep_client_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ZEP_API_KEY", raising=False)
    memory.get_zep_client.cache_clear()
    with pytest.raises(RuntimeError, match="ZEP_API_KEY"):
        memory.get_zep_client()


def test_get_zep_client_builds_zep_when_key_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZEP_API_KEY", "test-key")
    memory.get_zep_client.cache_clear()
    with patch("medtrace_agent.zep.memory.Zep") as zep_cls:
        mock_client = MagicMock()
        zep_cls.return_value = mock_client
        c1 = memory.get_zep_client()
        c2 = memory.get_zep_client()
        assert c1 is c2
        zep_cls.assert_called_once_with(api_key="test-key")


def test_ensure_user_conflict_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZEP_API_KEY", "k")
    memory.get_zep_client.cache_clear()
    mock_zep = MagicMock()
    mock_zep.user.add.side_effect = ConflictError(body=ApiError(message="exists"))
    with patch("medtrace_agent.zep.memory.Zep", return_value=mock_zep):
        memory.ensure_user("u1", "Jane Doe")
    mock_zep.user.add.assert_called_once()


def test_ensure_user_duplicate_message_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZEP_API_KEY", "k")
    memory.get_zep_client.cache_clear()
    mock_zep = MagicMock()
    mock_zep.user.add.side_effect = BadRequestError(
        body={"message": "User already exists with user_id u1"}
    )
    with patch("medtrace_agent.zep.memory.Zep", return_value=mock_zep):
        memory.ensure_user("u1", "Jane Doe")


def test_fetch_thread_context_sorts_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZEP_API_KEY", "k")
    memory.get_zep_client.cache_clear()
    mock_zep = MagicMock()
    ctx = MagicMock()
    ctx.context = "  ctx text  "
    mock_zep.thread.get_user_context.return_value = ctx
    m_old = Message(role="user", content="a", name="n", created_at="2020-01-01T00:00:00Z")
    m_new = Message(role="assistant", content="b", name="A", created_at="2021-01-01T00:00:00Z")
    mock_zep.thread.get.return_value = MagicMock(messages=[m_new, m_old])
    with patch("medtrace_agent.zep.memory.Zep", return_value=mock_zep):
        text, msgs = memory.fetch_thread_context("thread-1")
    assert text == "ctx text"
    assert [m.content for m in msgs] == ["a", "b"]


def test_append_turn_sends_user_and_assistant_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZEP_API_KEY", "k")
    memory.get_zep_client.cache_clear()
    mock_zep = MagicMock()
    with patch("medtrace_agent.zep.memory.Zep", return_value=mock_zep):
        memory.append_turn("t1", "Dr X", "hi", "hello there")
    call_kw = mock_zep.thread.add_messages.call_args
    assert call_kw[0][0] == "t1"
    sent = call_kw[1]["messages"]
    assert len(sent) == 2
    assert sent[0].role == "user" and sent[0].content == "hi"
    assert sent[1].role == "assistant" and sent[1].content == "hello there"
