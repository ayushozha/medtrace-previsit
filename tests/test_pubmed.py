"""Tests for medtrace_agent.integrations.pubmed (NCBI E-utilities)."""

from __future__ import annotations

import pytest

from medtrace_agent.integrations.pubmed import pubmed_search_summaries


def test_empty_query_returns_fixed_message() -> None:
    assert pubmed_search_summaries("") == "Empty PubMed query."
    assert pubmed_search_summaries("   ") == "Empty PubMed query."


def test_whitespace_only_query_treated_as_empty() -> None:
    assert pubmed_search_summaries("\n\t") == "Empty PubMed query."


@pytest.mark.integration
def test_pubmed_live_search_returns_structured_hits() -> None:
    """
    Hits real NCBI E-utilities (network required).

    Skips if NCBI returns an error string from our helper.
    """
    out = pubmed_search_summaries("diabetes mellitus therapy", max_results=3)
    if out.startswith("PubMed esearch failed:") or out.startswith("PubMed esummary failed:"):
        pytest.skip(f"NCBI unreachable or error: {out[:200]}")

    assert "No PubMed articles found" not in out, out
    assert "**PMID " in out, f"expected PMID bullets, got: {out[:500]}"
    assert "PubMed search (" in out
    assert "Literature only" in out or "peer-reviewed" in out


@pytest.mark.integration
def test_pubmed_impossible_query_no_hits_message() -> None:
    """Very specific nonsense query should yield no-articles message, not crash."""
    out = pubmed_search_summaries(
        "xyzabc123nonexistentterm999zzzzzqwertyunique2026nomatch",
        max_results=5,
    )
    if out.startswith("PubMed esearch failed:"):
        pytest.skip(f"NCBI unreachable: {out[:200]}")
    assert "No PubMed articles found" in out
