"""Unit tests for ``medtrace_agent.ontology.clinical``."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from medtrace_agent.ontology import clinical as ont


def test_ontology_constants_lengths() -> None:
    assert len(ont.ONTOLOGY_NODE_LABELS) <= 10
    assert len(ont.ONTOLOGY_EDGE_TYPES) <= 10


def test_apply_clinical_ontology_project_wide(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZEP_API_KEY", "k")
    from medtrace_agent.zep import memory

    memory.get_zep_client.cache_clear()
    mock_zep = MagicMock()
    with patch("medtrace_agent.ontology.clinical.get_zep_client", return_value=mock_zep):
        ont.apply_clinical_ontology(user_id=None, scope_to_user=False)
    mock_zep.graph.set_ontology.assert_called_once()
    kw = mock_zep.graph.set_ontology.call_args.kwargs
    assert "entities" in kw and "edges" in kw
    assert "user_ids" not in kw


def test_apply_clinical_ontology_scoped_user(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZEP_API_KEY", "k")
    mock_zep = MagicMock()
    with patch("medtrace_agent.ontology.clinical.get_zep_client", return_value=mock_zep):
        ont.apply_clinical_ontology("patient-9", scope_to_user=True)
    kw = mock_zep.graph.set_ontology.call_args.kwargs
    assert kw["user_ids"] == ["patient-9"]
