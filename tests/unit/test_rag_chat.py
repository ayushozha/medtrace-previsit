"""Unit tests for ``medtrace_agent.agents.rag_chat``."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from zep_cloud.types import Message as ZepMessage

from medtrace_agent.agents.rag_chat import (
    _zep_to_lc,
    chat_with_memory,
)


def test_zep_to_lc_maps_roles() -> None:
    zmsgs = [
        ZepMessage(role="user", content="u1", name="Alice"),
        ZepMessage(role="assistant", content="a1", name="Bot"),
    ]
    lc = _zep_to_lc(zmsgs)
    assert isinstance(lc[0], HumanMessage)
    assert lc[0].content == "u1"
    assert isinstance(lc[1], AIMessage)


def test_chat_with_memory_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FIREWORKS_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="FIREWORKS_API_KEY"):
        chat_with_memory(
            user_input="hi",
            user_display_name="x",
            zep_context="",
            thread_messages=[],
            model_name="m",
            api_key=None,
        )


def test_chat_with_memory_invokes_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FIREWORKS_API_KEY", "k")
    mock_resp = MagicMock()
    mock_resp.content = "Assistant reply"
    with patch("medtrace_agent.agents.rag_chat.fireworks_chat_client") as llm_cls:
        llm_cls.return_value.invoke.return_value = mock_resp
        out = chat_with_memory(
            user_input="question",
            user_display_name="Doc",
            zep_context="Memory: patient prefers morning visits.",
            thread_messages=[],
            model_name="test-model",
            api_key="k",
            base_url="https://example.invalid/v1/",
        )
    assert out == "Assistant reply"
    llm_cls.return_value.invoke.assert_called_once()
    msgs = llm_cls.return_value.invoke.call_args[0][0]
    assert any("Memory: patient" in str(m.content) for m in msgs)


def test_chat_with_memory_includes_doc_catalog_instructions() -> None:
    mock_resp = MagicMock()
    mock_resp.content = "ok"
    with patch("medtrace_agent.agents.rag_chat.fireworks_chat_client") as llm_cls:
        llm_cls.return_value.invoke.return_value = mock_resp
        chat_with_memory(
            user_input="q",
            user_display_name=None,
            zep_context="",
            thread_messages=[],
            model_name="m",
            api_key="k",
            base_url="https://x/v1/",
            document_catalog="doc1 — file.pdf",
        )
    msgs = llm_cls.return_value.invoke.call_args[0][0]
    sys_content = msgs[0].content
    assert "Ingested clinical documents" in sys_content
    assert "Sources:" in sys_content or "doc1" in sys_content
