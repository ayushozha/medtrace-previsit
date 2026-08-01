"""Unit tests for ``medtrace_agent.agents.deep_clinical``."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from medtrace_agent.agents import deep_clinical as dc


def test_deep_clinical_system_prompt_requires_pubmed_tool() -> None:
    assert "pubmed_search_literature" in dc.DEEP_CLINICAL_SYSTEM


def test_clinical_tool_session_restores_defaults() -> None:
    with dc.clinical_tool_session("u99", "t88"):
        assert dc._tool_user_id.get() == "u99"
        assert dc._tool_thread_id.get() == "t88"
    assert dc._tool_user_id.get() == ""
    assert dc._tool_thread_id.get() == ""


def test_extract_assistant_text_prefers_last_plain_ai_message() -> None:
    msgs = [
        HumanMessage(content="hi"),
        AIMessage(content="", tool_calls=[{"id": "1", "name": "x", "args": {}}]),
        AIMessage(content="Final answer here."),
    ]
    assert dc._extract_assistant_text(msgs) == "Final answer here."


def test_extract_assistant_text_empty_messages() -> None:
    assert dc._extract_assistant_text([]) == "(no response)"


def test_get_zep_thread_context_without_bind() -> None:
    out = dc.get_zep_thread_context.invoke({})  # type: ignore[attr-defined]
    assert "No thread_id" in out


def test_list_graph_episodes_without_user() -> None:
    out = dc.list_graph_episodes.invoke({"lastn": 10})  # type: ignore[attr-defined]
    assert "No user_id" in out


def test_get_zep_thread_context_uses_session_thread_id() -> None:
    with patch("medtrace_agent.agents.deep_clinical.fetch_thread_context") as mock_fetch:
        mock_fetch.return_value = ("ctx text", [])
        with dc.clinical_tool_session("patient-1", "thread-zz"):
            dc.get_zep_thread_context.invoke({})  # type: ignore[attr-defined]
        mock_fetch.assert_called_once_with("thread-zz")


@patch("medtrace_agent.agents.deep_clinical.create_deep_agent")
def test_get_compiled_clinical_agent_caches(
    mock_create: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FIREWORKS_API_KEY", "test-key")
    mock_create.return_value = MagicMock(name="graph")
    cp = MagicMock()
    g1 = dc.get_compiled_clinical_agent("model-x", cp)
    g2 = dc.get_compiled_clinical_agent("model-x", cp)
    assert g1 is g2
    mock_create.assert_called_once()


@patch("medtrace_agent.agents.deep_clinical.get_compiled_clinical_agent")
def test_run_clinical_deep_agent_turn(mock_get: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FIREWORKS_API_KEY", "k")
    graph = MagicMock()
    graph.invoke.return_value = {
        "messages": [
            HumanMessage(content="user"),
            AIMessage(content="Assistant output."),
        ]
    }
    mock_get.return_value = graph
    cp = MagicMock()
    text = dc.run_clinical_deep_agent_turn(
        user_id="u1",
        thread_id="t1",
        model_name="m",
        user_input="What meds?",
        document_catalog=None,
        checkpointer=cp,
    )
    assert text == "Assistant output."
    graph.invoke.assert_called_once()
