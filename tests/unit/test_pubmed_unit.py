"""Unit tests for PubMed helper with mocked HTTP/JSON (no network)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from medtrace_agent.integrations import pubmed


@pytest.fixture
def mock_ncbi_ok() -> dict[str, dict]:
    return {
        "esearch": {
            "esearchresult": {
                "idlist": ["123", "456"],
            }
        },
        "esummary": {
            "result": {
                "123": {
                    "title": "Study One",
                    "source": "Journal A",
                    "pubdate": "2024",
                    "authors": [{"name": "Smith J"}, {"name": "Doe A"}],
                },
                "456": {
                    "title": "Study Two",
                    "source": "Journal B",
                    "pubdate": "",
                    "authors": [],
                },
            }
        },
    }


def test_pubmed_search_summaries_success(mock_ncbi_ok: dict) -> None:
    calls: list[str] = []

    def fake_get_json(url: str, params: dict) -> dict:
        calls.append(url)
        if "esearch" in url:
            return mock_ncbi_ok["esearch"]
        return mock_ncbi_ok["esummary"]

    with patch.object(pubmed, "_get_json", side_effect=fake_get_json):
        out = pubmed.pubmed_search_summaries("diabetes", max_results=5)

    assert len(calls) == 2
    assert "PubMed search (" in out
    assert "**PMID 123**" in out
    assert "Study One" in out
    assert "_Literature only" in out or "peer-reviewed" in out


def test_pubmed_esearch_network_error() -> None:
    import urllib.error

    with patch.object(
        pubmed,
        "_get_json",
        side_effect=urllib.error.URLError("boom"),
    ):
        out = pubmed.pubmed_search_summaries("anything")
    assert out.startswith("PubMed esearch failed:")


def test_pubmed_max_results_clamped() -> None:
    captured: dict = {}

    def grab_params(url: str, params: dict) -> dict:
        captured.update(params)
        if "esearch" in url:
            return {"esearchresult": {"idlist": []}}
        return {"result": {}}

    with patch.object(pubmed, "_get_json", side_effect=grab_params):
        pubmed.pubmed_search_summaries("x", max_results=999)

    assert captured["retmax"] == "20"


def test_common_params_includes_api_key_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NCBI_EMAIL", "t@example.com")
    monkeypatch.setenv("NCBI_API_KEY", "secret")
    p = pubmed._common_params()
    assert p["email"] == "t@example.com"
    assert p["api_key"] == "secret"
