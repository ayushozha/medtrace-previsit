"""Unit tests for ``medtrace_agent.zep.graph`` (mocked Zep client)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from medtrace_agent.zep import graph as zg


def _ep(**kwargs: object) -> SimpleNamespace:
    base = dict(
        uuid_="ep-1",
        content="short",
        created_at="2024-01-01",
        processed=True,
        source="s",
        role="r",
        role_type="rt",
        thread_id="tid",
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_list_recent_episodes_truncates_long_content(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZEP_API_KEY", "k")
    long_text = "x" * 250
    mock_zep = MagicMock()
    mock_zep.graph.episode.get_by_user_id.return_value = MagicMock(
        episodes=[_ep(content=long_text)]
    )
    with patch("medtrace_agent.zep.graph.get_zep_client", return_value=mock_zep):
        rows = zg.list_recent_episodes("user-1", lastn=10)
    assert len(rows) == 1
    c = str(rows[0]["content"])
    assert c.endswith("...")
    assert len(c) == 200


def test_list_fact_edges_returns_cursor_when_full_page(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZEP_API_KEY", "k")
    edges = [
        SimpleNamespace(
            uuid_=f"e{i}",
            name="n",
            fact="f",
            valid_at=None,
            invalid_at=None,
            expired_at=None,
            created_at=None,
            episodes=[f"ep{i}"],
        )
        for i in range(3)
    ]
    mock_zep = MagicMock()
    mock_zep.graph.edge.get_by_user_id.return_value = edges
    with patch("medtrace_agent.zep.graph.get_zep_client", return_value=mock_zep):
        rows, cursor = zg.list_fact_edges("u1", limit=3)
    assert len(rows) == 3
    assert cursor == "e2"


def test_search_ontology_nodes_empty_labels(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZEP_API_KEY", "k")
    rows = zg.search_ontology_nodes("u1", "q", [])
    assert rows == []


def test_search_ontology_edges_empty_types(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZEP_API_KEY", "k")
    rows = zg.search_ontology_edges("u1", "q", [])
    assert rows == []


def test_search_ontology_nodes_calls_graph_search(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZEP_API_KEY", "k")
    node = SimpleNamespace(name="n1", labels=["Doctor"], summary="summary text")
    mock_zep = MagicMock()
    mock_zep.graph.search.return_value = MagicMock(nodes=[node], edges=None)
    with patch("medtrace_agent.zep.graph.get_zep_client", return_value=mock_zep):
        rows = zg.search_ontology_nodes("u1", "fever", ["Doctor"], limit=5)
    mock_zep.graph.search.assert_called_once()
    kw = mock_zep.graph.search.call_args.kwargs
    assert kw["scope"] == "nodes"
    assert kw["limit"] == 5
    assert len(rows) == 1
    assert rows[0]["node_name"] == "n1"


def test_episode_uuid_fallback_uuid_attr() -> None:
    ep = SimpleNamespace(uuid_="", uuid="fallback-id")
    assert zg._episode_uuid(ep) == "fallback-id"
