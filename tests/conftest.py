"""Shared pytest fixtures for backend tests."""

from __future__ import annotations

from medtrace_agent.env import load_repo_env

load_repo_env()

# Langtrace before any LangChain / LangGraph agent imports.
from medtrace_agent.tracing import init_langtrace

init_langtrace()

import pytest


@pytest.fixture(autouse=True)
def clear_zep_lru_cache() -> None:
    """Isolate tests that patch ``get_zep_client`` or env-based client creation."""
    from medtrace_agent.zep import memory

    memory.get_zep_client.cache_clear()
    yield
    memory.get_zep_client.cache_clear()


@pytest.fixture(autouse=True)
def clear_deep_agent_graph_cache() -> None:
    from medtrace_agent.agents import deep_clinical

    deep_clinical.GRAPH_CACHE.clear()
    yield
    deep_clinical.GRAPH_CACHE.clear()
