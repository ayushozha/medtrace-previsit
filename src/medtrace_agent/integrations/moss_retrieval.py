"""Local Moss session retrieval for patient chart and current-call evidence."""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
from typing import Any

from medtrace_agent.integrations.sponsor_error import SponsorIntegrationError

_URL_OVERRIDES = ("MOSS_QUERY_URL", "MOSS_INDEX_URL", "MOSS_AUTH_URL", "MOSS_INDEX_AUTH_URL")


def configuration_status() -> dict[str, object]:
    required = (
        "MOSS_PROJECT_ID",
        "MOSS_PROJECT_KEY",
        "MOSS_INDEX_NAME",
        "MOSS_MODEL_ID",
        "MOSS_DISABLE_TELEMETRY",
    )
    missing = [name for name in required if not (os.environ.get(name) or "").strip()]
    telemetry = (os.environ.get("MOSS_DISABLE_TELEMETRY") or "").strip().lower()
    if telemetry and telemetry != "1":
        missing.append("MOSS_DISABLE_TELEMETRY must be 1 for the synthetic demo")
    missing.extend(
        f"{name} must be unset for the sponsor-only route"
        for name in _URL_OVERRIDES
        if (os.environ.get(name) or "").strip()
    )
    return {"configured": not missing, "missing": missing}


def _required(name: str) -> str:
    value = (os.environ.get(name) or "").strip()
    if not value:
        raise SponsorIntegrationError(
            "moss", f"{name} is required for the real Moss retrieval path.", status_code=503
        )
    return value


def _index_name(base: str, patient_id: str, checkin_id: str) -> str:
    suffix = hashlib.sha256(f"{patient_id}:{checkin_id}".encode("utf-8")).hexdigest()[:20]
    safe_base = re.sub(r"[^a-z0-9-]+", "-", base.lower()).strip("-")
    if not safe_base:
        raise SponsorIntegrationError(
            "moss", "MOSS_INDEX_NAME must contain letters or numbers.", status_code=503
        )
    return f"{safe_base[:43]}-{suffix}"


async def retrieve_context(
    *,
    patient_id: str,
    checkin_id: str,
    documents: list[dict[str, Any]],
    query: str,
) -> dict[str, Any]:
    """Index/query locally; the session is intentionally never pushed to Moss Cloud."""
    if (os.environ.get("MOSS_DISABLE_TELEMETRY") or "").strip() != "1":
        raise SponsorIntegrationError(
            "moss", "MOSS_DISABLE_TELEMETRY=1 is required for the synthetic demo.", status_code=503
        )
    configured_override = next(
        (name for name in _URL_OVERRIDES if (os.environ.get(name) or "").strip()), None
    )
    if configured_override:
        raise SponsorIntegrationError(
            "moss",
            f"{configured_override} must be unset for the sponsor-only route.",
            status_code=503,
        )
    try:
        from moss import DocumentInfo, MossClient, QueryOptions
    except ImportError as exc:
        raise SponsorIntegrationError(
            "moss", "The pinned Moss runtime is not installed on the API server.", status_code=503
        ) from exc

    try:
        top_k = int(os.environ.get("MOSS_QUERY_TOP_K") or "5")
        alpha = float(os.environ.get("MOSS_QUERY_ALPHA") or "0.8")
        timeout = float(os.environ.get("MOSS_TIMEOUT_SECONDS") or "30")
    except ValueError as exc:
        raise SponsorIntegrationError("moss", "Moss numeric configuration is invalid.", status_code=500) from exc
    if not 0 <= alpha <= 1 or top_k < 1 or timeout <= 0:
        raise SponsorIntegrationError(
            "moss",
            "MOSS_QUERY_ALPHA must be 0-1 and query limits must be positive.",
            status_code=500,
        )

    client = MossClient(_required("MOSS_PROJECT_ID"), _required("MOSS_PROJECT_KEY"))
    name = _index_name(_required("MOSS_INDEX_NAME"), patient_id, checkin_id)
    model_id = _required("MOSS_MODEL_ID")
    moss_docs = [
        DocumentInfo(
            id=str(document["id"]),
            text=str(document["text"]),
            metadata={str(k): str(v) for k, v in (document.get("metadata") or {}).items()},
        )
        for document in documents
        if str(document.get("text") or "").strip()
    ]
    if not moss_docs:
        raise SponsorIntegrationError("moss", "No patient or transcript evidence was available to index.")
    try:
        session = await asyncio.wait_for(client.session(name, model_id=model_id), timeout=timeout)
        await asyncio.wait_for(session.add_docs(moss_docs), timeout=timeout)
        result = await asyncio.wait_for(
            session.query(query, QueryOptions(top_k=max(1, min(top_k, 10)), alpha=alpha)),
            timeout=timeout,
        )
    except Exception as exc:
        raise SponsorIntegrationError("moss", f"Moss retrieval failed: {exc}") from exc

    evidence = [
        {
            "id": str(doc.id),
            "text": str(doc.text),
            "score": float(doc.score),
            "source": str(doc.index_name or name),
        }
        for doc in result.docs
    ]
    return {
        "index_name": name,
        "query": query,
        "time_taken_ms": result.time_taken_ms,
        "evidence": evidence,
        "persisted": False,
    }
