"""
Optional InsForge persistence: Storage uploads + documents / chart_subjects / chat_sessions rows.

Uses InsForge HTTP API (same routes as @insforge/sdk): ``/api/database/records/{table}``,
``/api/storage/buckets/{bucket}/objects``. Requires the server-side ``INSFORGE_API_KEY``
(do not expose it in client apps).

Environment:
  INSFORGE_URL           — e.g. https://{appkey}.us-east.insforge.app
  INSFORGE_API_KEY       — project API key (admin; keep secret)
  INSFORGE_ANON_KEY      — optional; sent as apikey header when set
  INSFORGE_PROFILE_ID    — uuid matching public.profiles.id / auth.users.id
  INSFORGE_DOCUMENTS_BUCKET — default medtrace-documents
  MEDTRACE_LOCAL_MOCK    — set to 1/true to use file-backed local fixtures
                           instead of InsForge (see ``local_store``)
"""

from __future__ import annotations

import functools
import mimetypes
import os
from collections.abc import Callable
from typing import Any, Literal, TypeVar

import httpx

from medtrace_agent import local_store
from medtrace_agent.local_store import local_mock_enabled, local_profile_id

DocumentKind = Literal["clinical_pdf", "radiology_note", "conversation_note"]

_F = TypeVar("_F", bound=Callable[..., Any])


def local_mock_fallback(fn: _F) -> _F:
    """Route the call to the same-named ``local_store`` function when local mock is on.

    Every persistence helper here has a signature-matching twin in ``local_store``; this
    keeps the two in lockstep instead of repeating the branch in each function. Decorated
    functions are keyword-only, so the arguments forward verbatim.
    """

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        if local_mock_enabled():
            return getattr(local_store, fn.__name__)(*args, **kwargs)
        return fn(*args, **kwargs)

    return wrapper  # type: ignore[return-value]


def _remote_insforge_configured() -> bool:
    url = (os.environ.get("INSFORGE_URL") or "").strip()
    key = (os.environ.get("INSFORGE_API_KEY") or "").strip()
    profile = (os.environ.get("INSFORGE_PROFILE_ID") or "").strip()
    return bool(url and key and profile)


def insforge_persistence_enabled() -> bool:
    """True when either real InsForge creds are set or local mock mode is on."""
    return local_mock_enabled() or _remote_insforge_configured()


def _base_url() -> str:
    return (os.environ.get("INSFORGE_URL") or "").rstrip("/")


def _api_key() -> str:
    return (os.environ.get("INSFORGE_API_KEY") or "").strip()


def _anon_key() -> str:
    return (os.environ.get("INSFORGE_ANON_KEY") or "").strip()


def _profile_id() -> str:
    if local_mock_enabled():
        return local_profile_id()
    return (os.environ.get("INSFORGE_PROFILE_ID") or "").strip()


def documents_bucket() -> str:
    return (os.environ.get("INSFORGE_DOCUMENTS_BUCKET") or "medtrace-documents").strip()


def _headers() -> dict[str, str]:
    key = _api_key()
    h = {
        "Authorization": f"Bearer {key}",
        "apikey": _anon_key() or key,
    }
    return h


def _records(table: str) -> str:
    return f"{_base_url()}/api/database/records/{table}"


@local_mock_fallback
def upload_bytes_to_bucket(
    file_bytes: bytes,
    filename: str,
    *,
    content_type: str | None = None,
    bucket: str | None = None,
) -> dict[str, Any]:
    """Upload file bytes; returns JSON with bucket, key, url, size, mimeType, uploadedAt."""
    b = bucket or documents_bucket()
    ct = content_type or (mimetypes.guess_type(filename)[0] or "application/octet-stream")
    url = f"{_base_url()}/api/storage/buckets/{b}/objects"
    # Multipart field name must be "file" per API error message.
    files = {"file": (filename, file_bytes, ct)}
    with httpx.Client(timeout=120.0) as client:
        r = client.post(url, headers=_headers(), files=files)
        r.raise_for_status()
        return r.json()


@local_mock_fallback
def ensure_chart_subject_id(
    *,
    zep_user_id: str,
    display_name: str,
    metadata: dict[str, Any] | None = None,
) -> str | None:
    """Return chart_subjects.id for this profile + zep_user_id; insert or update metadata."""
    if not insforge_persistence_enabled():
        return None
    pid = _profile_id()
    meta = metadata if metadata is not None else {}
    params = {
        "owner_profile_id": f"eq.{pid}",
        "zep_user_id": f"eq.{zep_user_id}",
        "select": "id",
        "limit": "1",
    }
    with httpx.Client(timeout=30.0) as client:
        r = client.get(_records("chart_subjects"), headers=_headers(), params=params)
        r.raise_for_status()
        rows = r.json()
        if rows and isinstance(rows, list) and len(rows) >= 1:
            row = rows[0]
            sid = row.get("id") if isinstance(row, dict) else None
            if sid:
                patch: dict[str, Any] = {
                    "display_name": display_name or None,
                    "metadata": meta,
                }
                p = client.patch(
                    f"{_records('chart_subjects')}?id=eq.{sid}",
                    headers=_headers(),
                    json=patch,
                )
                p.raise_for_status()
                return str(sid)
        payload = [
            {
                "owner_profile_id": pid,
                "zep_user_id": zep_user_id,
                "display_name": display_name or None,
                "metadata": meta,
            }
        ]
        ins = client.post(
            _records("chart_subjects"),
            headers={**_headers(), "Prefer": "return=representation"},
            json=payload,
        )
        ins.raise_for_status()
        out = ins.json()
        if isinstance(out, list) and out and isinstance(out[0], dict):
            rid = out[0].get("id")
            return str(rid) if rid else None
    return None


@local_mock_fallback
def upsert_chat_session_row(
    *,
    zep_thread_id: str,
    chart_subject_id: str | None,
    title: str | None = None,
) -> None:
    """Insert or patch chat_sessions row for this Zep thread."""
    if not insforge_persistence_enabled():
        return
    pid = _profile_id()
    params = {"zep_thread_id": f"eq.{zep_thread_id}", "select": "id", "limit": "1"}
    with httpx.Client(timeout=30.0) as client:
        r = client.get(_records("chat_sessions"), headers=_headers(), params=params)
        r.raise_for_status()
        existing = r.json()
        body = {
            "profile_id": pid,
            "chart_subject_id": chart_subject_id,
            "zep_thread_id": zep_thread_id,
            "title": title,
        }
        if existing and isinstance(existing, list) and len(existing) >= 1:
            sid = existing[0].get("id")
            if sid:
                p = client.patch(
                    f"{_records('chat_sessions')}?id=eq.{sid}",
                    headers=_headers(),
                    json={k: v for k, v in body.items() if k != "zep_thread_id"},
                )
                p.raise_for_status()
                return
        ins = client.post(_records("chat_sessions"), headers=_headers(), json=[body])
        ins.raise_for_status()


@local_mock_fallback
def insert_document_record(
    *,
    doc_id: str,
    filename: str,
    document_kind: DocumentKind,
    storage_bucket: str,
    storage_key: str,
    storage_url: str | None,
    chart_subject_id: str | None,
    extract_mode: str | None = None,
    episode_count: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not insforge_persistence_enabled():
        return None
    row = {
        "doc_id": doc_id,
        "profile_id": _profile_id(),
        "chart_subject_id": chart_subject_id,
        "filename": filename,
        "document_kind": document_kind,
        "extract_mode": extract_mode,
        "episode_count": episode_count,
        "storage_bucket": storage_bucket,
        "storage_key": storage_key,
        "storage_url": storage_url,
        "metadata": metadata or {},
    }
    with httpx.Client(timeout=60.0) as client:
        r = client.post(
            _records("documents"),
            headers={**_headers(), "Prefer": "return=representation"},
            json=[row],
        )
        r.raise_for_status()
        out = r.json()
        if isinstance(out, list) and out:
            return out[0] if isinstance(out[0], dict) else None
    return None


@local_mock_fallback
def list_chart_subjects(
    *,
    profile_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return all chart_subjects rows for a profile (default: INSFORGE_PROFILE_ID).

    Used by the React Frontend's patient directory.
    """
    if not insforge_persistence_enabled():
        return []
    pid = profile_id or _profile_id()
    params = {
        "owner_profile_id": f"eq.{pid}",
        "select": "*",
        "order": "created_at.desc",
    }
    with httpx.Client(timeout=30.0) as client:
        r = client.get(_records("chart_subjects"), headers=_headers(), params=params)
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else []


@local_mock_fallback
def get_chart_subject(
    *,
    chart_subject_id: str,
) -> dict[str, Any] | None:
    """Fetch a single chart_subjects row by id (must belong to INSFORGE_PROFILE_ID)."""
    if not insforge_persistence_enabled():
        return None
    pid = _profile_id()
    params = {
        "id": f"eq.{chart_subject_id}",
        "owner_profile_id": f"eq.{pid}",
        "select": "*",
        "limit": "1",
    }
    with httpx.Client(timeout=30.0) as client:
        r = client.get(_records("chart_subjects"), headers=_headers(), params=params)
        r.raise_for_status()
        rows = r.json()
        if isinstance(rows, list) and rows:
            return rows[0] if isinstance(rows[0], dict) else None
    return None


@local_mock_fallback
def update_chart_subject_metadata(
    *,
    chart_subject_id: str,
    metadata_patch: dict[str, Any],
) -> dict[str, Any] | None:
    """Merge ``metadata_patch`` into ``chart_subjects.metadata`` (read-modify-write)."""
    if not insforge_persistence_enabled():
        return None
    existing = get_chart_subject(chart_subject_id=chart_subject_id)
    if not existing:
        return None
    current_meta = existing.get("metadata") or {}
    if not isinstance(current_meta, dict):
        current_meta = {}
    merged = {**current_meta, **metadata_patch}
    with httpx.Client(timeout=30.0) as client:
        r = client.patch(
            f"{_records('chart_subjects')}?id=eq.{chart_subject_id}",
            headers={**_headers(), "Prefer": "return=representation"},
            json={"metadata": merged},
        )
        r.raise_for_status()
        out = r.json()
        if isinstance(out, list) and out and isinstance(out[0], dict):
            return out[0]
    return None


@local_mock_fallback
def list_chat_sessions_for_chart(
    *,
    chart_subject_id: str,
) -> list[dict[str, Any]]:
    """Return chat_sessions rows for a chart_subject (most recent first)."""
    if not insforge_persistence_enabled():
        return []
    pid = _profile_id()
    params = {
        "profile_id": f"eq.{pid}",
        "chart_subject_id": f"eq.{chart_subject_id}",
        "select": "*",
        "order": "updated_at.desc",
    }
    with httpx.Client(timeout=30.0) as client:
        r = client.get(_records("chat_sessions"), headers=_headers(), params=params)
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else []


@local_mock_fallback
def find_chat_session_by_thread(
    *,
    zep_thread_id: str,
) -> dict[str, Any] | None:
    """Return the chat_sessions row for a Zep thread, or None."""
    if not insforge_persistence_enabled():
        return None
    params = {
        "zep_thread_id": f"eq.{zep_thread_id}",
        "profile_id": f"eq.{_profile_id()}",
        "select": "*",
        "limit": "1",
    }
    with httpx.Client(timeout=30.0) as client:
        r = client.get(_records("chat_sessions"), headers=_headers(), params=params)
        r.raise_for_status()
        rows = r.json()
        if isinstance(rows, list) and rows and isinstance(rows[0], dict):
            return rows[0]
    return None


@local_mock_fallback
def touch_chat_session(
    *,
    zep_thread_id: str,
) -> None:
    """Bump ``chat_sessions.updated_at`` after a new turn is appended."""
    if not insforge_persistence_enabled():
        return
    from datetime import datetime, timezone

    now_iso = datetime.now(timezone.utc).isoformat()
    with httpx.Client(timeout=30.0) as client:
        client.patch(
            f"{_records('chat_sessions')}?zep_thread_id=eq.{zep_thread_id}",
            headers=_headers(),
            json={"updated_at": now_iso},
        )


@local_mock_fallback
def fetch_documents_registry(
    *,
    chart_subject_id: str | None = None,
) -> list[dict[str, Any]]:
    """Load document rows for INSFORGE_PROFILE_ID; optional chart_subject filter."""
    if not insforge_persistence_enabled():
        return []
    pid = _profile_id()
    params: dict[str, str] = {
        "profile_id": f"eq.{pid}",
        "select": "*",
        "order": "uploaded_at.desc",
    }
    if chart_subject_id:
        params["chart_subject_id"] = f"eq.{chart_subject_id}"
    with httpx.Client(timeout=60.0) as client:
        r = client.get(_records("documents"), headers=_headers(), params=params)
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else []


def document_row_to_ingested_doc(row: dict[str, Any]) -> dict[str, Any]:
    """Map a DB row to the compact ``ingested_docs`` catalog shape used in LLM prompts."""
    kind = row.get("document_kind") or ""
    uploaded = row.get("uploaded_at") or ""
    if isinstance(uploaded, str):
        uploaded_at_utc = uploaded.replace("T", " ")[:19] + " UTC" if "T" in uploaded else uploaded
    else:
        uploaded_at_utc = str(uploaded)
    ingest_kind_map = {
        "clinical_pdf": "pdf",
        "radiology_note": "radiology_note",
        "conversation_note": "session_note",
    }
    return {
        "doc_id": row.get("doc_id"),
        "filename": row.get("filename"),
        "uploaded_at_utc": uploaded_at_utc,
        "episode_count": row.get("episode_count") or 0,
        "ingest_kind": ingest_kind_map.get(kind, kind or "unknown"),
        "document_kind": kind,
        "storage_url": row.get("storage_url"),
        "storage_key": row.get("storage_key"),
    }


def persist_ingest_with_upload(
    *,
    file_bytes: bytes,
    filename: str,
    doc_id: str,
    zep_user_id: str,
    patient_display_name: str,
    document_kind: DocumentKind,
    extract_mode: str | None,
    episode_count: int,
) -> dict[str, Any] | None:
    """
    Upload bytes to InsForge Storage, ensure chart_subject, insert documents row.
    Returns inserted row or None if persistence disabled / failure (caller may catch).
    """
    if not insforge_persistence_enabled():
        return None
    chart_id = ensure_chart_subject_id(
        zep_user_id=zep_user_id,
        display_name=patient_display_name,
    )
    up = upload_bytes_to_bucket(file_bytes, filename)
    bucket = up.get("bucket") or documents_bucket()
    key = up.get("key")
    url = up.get("url")
    if not key:
        raise RuntimeError("Storage upload missing key in response")
    return insert_document_record(
        doc_id=doc_id,
        filename=filename,
        document_kind=document_kind,
        storage_bucket=str(bucket),
        storage_key=str(key),
        storage_url=str(url) if url else None,
        chart_subject_id=chart_id,
        extract_mode=extract_mode,
        episode_count=episode_count,
        metadata={},
    )
