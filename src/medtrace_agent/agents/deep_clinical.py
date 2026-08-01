"""LangChain Deep Agent path: Zep tools + PubMed — demo clinical reasoning (non-diagnostic)."""

from __future__ import annotations

import contextvars
from contextlib import contextmanager
from typing import Any, Iterator

from langchain_core.messages import AIMessage, HumanMessage
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver

from deepagents import create_deep_agent

from medtrace_agent.fireworks_config import fireworks_chat_client
from medtrace_agent.integrations.pubmed import pubmed_search_summaries
from medtrace_agent.ontology.clinical import ONTOLOGY_EDGE_TYPES, ONTOLOGY_NODE_LABELS
from medtrace_agent.zep.graph import (
    list_fact_edges,
    list_recent_episodes,
    rows_to_csv,
    search_ontology_edges,
    search_ontology_nodes,
)
from medtrace_agent.zep.memory import fetch_thread_context

# Request-scoped bind for Zep tools (safe with concurrent requests / asyncio tasks).
_tool_user_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "medtrace_tool_user_id", default=""
)
_tool_thread_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "medtrace_tool_thread_id", default=""
)

GRAPH_CACHE: dict[tuple[str, int], Any] = {}


@contextmanager
def clinical_tool_session(user_id: str, thread_id: str) -> Iterator[None]:
    """Bind ``user_id`` / ``thread_id`` for Deep Agent Zep tools for the duration of ``graph.invoke``."""
    tok_u = _tool_user_id.set(user_id)
    tok_t = _tool_thread_id.set(thread_id)
    try:
        yield
    finally:
        _tool_user_id.reset(tok_u)
        _tool_thread_id.reset(tok_t)


DEEP_CLINICAL_SYSTEM = """You are a clinical reasoning assistant in an educational demo (NOT a licensed medical device).

You MUST:
- Use tools to ground patient-specific claims: call `get_zep_thread_context`, `list_graph_episodes`, `list_temporal_edges`, and ontology search tools (`search_patient_ontology_nodes`, `search_patient_ontology_edges`) before stating what happened on the patient's timeline.
- For background mechanisms, guidelines-oriented questions, or whenever external peer-reviewed evidence would materially improve the answer, call `pubmed_search_literature` with a focused query — do not wait for the user to say “PubMed” if literature is clearly relevant.
- After PubMed results return, **synthesize**: tie titles/findings to the patient-specific picture only where justified; clearly separate **what the Zep graph shows for this patient** from **what the literature says generally**.
- Present **differential considerations** and **questions to clarify** — never a definitive diagnosis or treatment plan.
- Label inference clearly: 'possible consideration', 'would warrant evaluation if ...', and cite PMIDs when using PubMed output.
- Be concise. Avoid filesystem/task tools unless truly needed for the user's request; prefer Zep + PubMed tools for clinical questions.

You MUST NOT:
- Claim certainty about diagnosis or replace a clinician.
- Invent symptoms, dates, or lab values not supported by tool output."""


@tool
def get_zep_thread_context() -> str:
    """Synthesized long-term + relevant memory string from Zep for the current chat thread."""
    tid = _tool_thread_id.get()
    if not tid:
        return "No thread_id bound."
    ctx, _ = fetch_thread_context(tid)
    return (ctx or "").strip() or "(empty context)"


@tool
def list_graph_episodes(lastn: int = 25) -> str:
    """Recent Zep graph episodes for the current patient (text snippets ingested from chat/PDFs)."""
    uid = _tool_user_id.get()
    if not uid:
        return "No user_id bound."
    lastn = max(1, min(int(lastn), 80))
    rows = list_recent_episodes(uid, lastn=lastn)
    if not rows:
        return "No episodes found."
    return rows_to_csv(rows, max_rows=60)[:14000]


@tool
def list_temporal_edges(limit: int = 40) -> str:
    """Temporal fact edges from the patient's Zep graph (facts, validity times)."""
    uid = _tool_user_id.get()
    if not uid:
        return "No user_id bound."
    limit = max(1, min(int(limit), 100))
    rows, _ = list_fact_edges(uid, limit=limit)
    if not rows:
        return "No edges found."
    return rows_to_csv(rows)[:14000]


@tool
def search_patient_ontology_nodes(query: str) -> str:
    """Semantic search over ontology node types (conditions, meds, etc.) for this patient."""
    uid = _tool_user_id.get()
    if not uid:
        return "No user_id bound."
    q = (query or "").strip() or "patient clinical"
    rows = search_ontology_nodes(
        uid,
        q,
        node_labels=list(ONTOLOGY_NODE_LABELS),
        limit=15,
    )
    if not rows:
        return "No matching ontology nodes (ontology may be unset or still processing)."
    return rows_to_csv(rows)[:12000]


@tool
def search_patient_ontology_edges(query: str) -> str:
    """Semantic search over ontology edge types (HAS_CONDITION, etc.) for this patient."""
    uid = _tool_user_id.get()
    if not uid:
        return "No user_id bound."
    q = (query or "").strip() or "patient clinical"
    rows = search_ontology_edges(
        uid,
        q,
        edge_types=list(ONTOLOGY_EDGE_TYPES),
        limit=15,
    )
    if not rows:
        return "No matching ontology edges."
    return rows_to_csv(rows)[:12000]


@tool
def pubmed_search_literature(query: str, max_results: int = 8) -> str:
    """Search PubMed for peer-reviewed literature summaries (titles + PMID). Demo only."""
    return pubmed_search_summaries(query, max_results=max_results)


ALL_CLINICAL_TOOLS = [
    get_zep_thread_context,
    list_graph_episodes,
    list_temporal_edges,
    search_patient_ontology_nodes,
    search_patient_ontology_edges,
    pubmed_search_literature,
]


def _make_llm(model_name: str) -> ChatOpenAI:
    return fireworks_chat_client(model_name, temperature=0.2)


def get_compiled_clinical_agent(model_name: str, checkpointer: MemorySaver):
    """One compiled graph per (model_name, checkpointer id) for stable checkpoint state."""
    key = (model_name, id(checkpointer))
    if key in GRAPH_CACHE:
        return GRAPH_CACHE[key]
    llm = _make_llm(model_name)
    graph = create_deep_agent(
        model=llm,
        tools=list(ALL_CLINICAL_TOOLS),
        system_prompt=DEEP_CLINICAL_SYSTEM,
        checkpointer=checkpointer,
    )
    GRAPH_CACHE[key] = graph
    return graph


def _extract_assistant_text(messages: list[Any]) -> str:
    for m in reversed(messages):
        if not isinstance(m, AIMessage):
            continue
        tc = getattr(m, "tool_calls", None) or []
        if tc:
            continue
        c = m.content
        if isinstance(c, str) and c.strip():
            return c.strip()
        if isinstance(c, list):
            parts: list[str] = []
            for block in c:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(str(block.get("text", "")))
                elif isinstance(block, str):
                    parts.append(block)
            if parts:
                return "".join(parts).strip()
    if messages:
        return str(getattr(messages[-1], "content", messages[-1]))
    return "(no response)"


def run_clinical_deep_agent_turn(
    *,
    user_id: str,
    thread_id: str,
    model_name: str,
    user_input: str,
    document_catalog: str | None,
    checkpointer: MemorySaver,
) -> str:
    """
    Run one Deep Agent turn with Zep/PubMed tools. Thread-scoped checkpoint via ``thread_id``.
    """
    graph = get_compiled_clinical_agent(model_name, checkpointer)

    body = user_input.strip()
    cat = (document_catalog or "").strip()
    if cat:
        body = (
            "[Ingested clinical documents — cite doc_id / filename when using these facts]\n"
            + cat
            + "\n\n---\n\nUser message:\n"
            + body
        )

    with clinical_tool_session(user_id, thread_id):
        result = graph.invoke(
            {"messages": [HumanMessage(content=body)]},
            config={
                "configurable": {"thread_id": thread_id},
                "recursion_limit": 50,
            },
        )
    msgs = result.get("messages") or []
    return _extract_assistant_text(msgs)
