"""Read-only Zep graph helpers (episodes, temporal edges, ontology search).

Every function returns ``list[dict]`` — plain rows the FastAPI routers validate into
Pydantic models and the deep-agent tools serialise to CSV.
"""

from __future__ import annotations

import csv
import io
from typing import Any

from zep_cloud.types.search_filters import SearchFilters

from medtrace_agent.zep.memory import get_zep_client


def _episode_uuid(ep: Any) -> str:
    return getattr(ep, "uuid_", None) or getattr(ep, "uuid", "") or ""


def _edge_uuid(edge: Any) -> str:
    return getattr(edge, "uuid_", None) or getattr(edge, "uuid", "") or ""


def list_recent_episodes(
    user_id: str,
    lastn: int = 25,
    *,
    truncate_chars: int | None = 200,
) -> list[dict[str, Any]]:
    """Return recent Zep episodes as plain row dicts.

    The deep-agent tool wants short snippets (default ``truncate_chars=200``);
    the FastAPI clinical router needs full content to run lab regex extraction
    and passes ``truncate_chars=None`` to disable truncation.
    """
    client = get_zep_client()
    resp = client.graph.episode.get_by_user_id(user_id, lastn=lastn)
    episodes = resp.episodes or []
    rows: list[dict[str, Any]] = []
    for ep in episodes:
        raw = (ep.content or "").replace("\n", " ")
        if truncate_chars and len(raw) > truncate_chars:
            content = raw[: max(truncate_chars - 3, 0)] + "..."
        else:
            content = raw
        rows.append(
            {
                "uuid": _episode_uuid(ep),
                "created_at": ep.created_at,
                "processed": ep.processed,
                "source": ep.source,
                "role": ep.role,
                "role_type": ep.role_type,
                "thread_id": ep.thread_id,
                "content": content,
            }
        )
    return rows


def list_fact_edges(
    user_id: str, limit: int = 50, uuid_cursor: str | None = None
) -> tuple[list[dict[str, Any]], str | None]:
    """
    Returns temporal fact rows and an optional cursor for the next page
    (last edge uuid when the page is full).
    """
    client = get_zep_client()
    kwargs: dict[str, Any] = {}
    if limit is not None:
        kwargs["limit"] = limit
    if uuid_cursor:
        kwargs["uuid_cursor"] = uuid_cursor

    edges = client.graph.edge.get_by_user_id(user_id, **kwargs)
    rows: list[dict[str, Any]] = []
    for edge in edges:
        ep_list = edge.episodes or []
        rows.append(
            {
                "uuid": _edge_uuid(edge),
                "name": edge.name,
                "fact": edge.fact,
                "valid_at": edge.valid_at,
                "invalid_at": edge.invalid_at,
                "expired_at": edge.expired_at,
                "created_at": edge.created_at,
                "episode_links": len(ep_list),
                "episode_uuids": ",".join(ep_list[:5]) + ("…" if len(ep_list) > 5 else ""),
            }
        )

    next_cursor: str | None = None
    if edges and len(edges) >= limit:
        next_cursor = _edge_uuid(edges[-1])

    return rows, next_cursor


def search_ontology_nodes(
    user_id: str,
    query: str,
    node_labels: list[str],
    *,
    limit: int = 15,
) -> list[dict[str, Any]]:
    """Filtered graph search scoped to custom entity labels (ontology highlights)."""
    if not node_labels:
        return []
    client = get_zep_client()
    filters = SearchFilters(node_labels=node_labels)
    q = (query or "").strip() or "patient clinical record"
    res = client.graph.search(
        user_id=user_id,
        query=q,
        scope="nodes",
        limit=min(max(limit, 1), 50),
        search_filters=filters,
    )
    nodes = res.nodes or []
    rows: list[dict[str, Any]] = []
    for node in nodes:
        labels = ",".join(node.labels or [])
        summ = str(getattr(node, "summary", "") or "")
        rows.append(
            {
                "node_name": getattr(node, "name", ""),
                "labels": labels,
                "summary": summ[:500],
            }
        )
    return rows


def search_ontology_edges(
    user_id: str,
    query: str,
    edge_types: list[str],
    *,
    limit: int = 15,
) -> list[dict[str, Any]]:
    """Filtered graph search scoped to custom edge types."""
    if not edge_types:
        return []
    client = get_zep_client()
    filters = SearchFilters(edge_types=edge_types)
    q = (query or "").strip() or "patient clinical record"
    res = client.graph.search(
        user_id=user_id,
        query=q,
        scope="edges",
        limit=min(max(limit, 1), 50),
        search_filters=filters,
    )
    edges = res.edges or []
    rows: list[dict[str, Any]] = []
    for edge in edges:
        rows.append(
            {
                "edge_type": edge.name,
                "fact": edge.fact,
                "valid_at": edge.valid_at,
                "invalid_at": edge.invalid_at,
            }
        )
    return rows


def rows_to_csv(rows: list[dict[str, Any]], *, max_rows: int | None = None) -> str:
    """Serialise rows to CSV for LLM tool output. Empty rows produce an empty string."""
    if not rows:
        return ""
    if max_rows is not None:
        rows = rows[:max_rows]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()
