"""Shared helpers for Medplum-backed API routers."""

from __future__ import annotations

from typing import NoReturn

from fastapi import HTTPException, status

from medtrace_agent.medplum import MedplumError


def raise_medplum_http(exc: MedplumError) -> NoReturn:
    if exc.status_code == 404:
        code = status.HTTP_404_NOT_FOUND
    elif exc.status_code in {401, 403}:
        code = status.HTTP_503_SERVICE_UNAVAILABLE
    elif exc.status_code == 422:
        code = status.HTTP_422_UNPROCESSABLE_ENTITY
    else:
        code = status.HTTP_502_BAD_GATEWAY
    raise HTTPException(status_code=code, detail=str(exc)) from exc


def safe_processing_error(exc: Exception) -> str:
    """Return an actionable client-visible error without provider payloads or secrets."""
    if isinstance(exc, MedplumError):
        return str(exc)
    if isinstance(exc, (UnicodeDecodeError, ValueError)):
        return str(exc)[:300]
    return "Document processing or AI-memory projection failed; the source document was preserved."


def document_catalog(documents: list[dict[str, object]]) -> str:
    lines = []
    for doc in documents[:25]:
        lines.append(
            f"- doc_id `{doc.get('doc_id') or '?'}` — {doc.get('filename') or '?'} "
            f"({doc.get('document_kind') or '?'}, {doc.get('episode_count') or 0} episodes)"
        )
    return "\n".join(lines)


def deterministic_summary(
    patient_name: str,
    conditions: list[dict[str, object]],
    medications: list[dict[str, object]],
    allergies: list[dict[str, object]],
    labs: list[dict[str, object]],
) -> str:
    parts = [f"{patient_name}'s chart currently includes {len(conditions)} condition(s)"]
    if medications:
        parts.append(f"{len(medications)} medication statement(s)")
    if allergies:
        parts.append(f"{len(allergies)} documented allergy record(s)")
    abnormal = [lab for lab in labs if lab.get("status") not in {None, "Normal"}]
    if abnormal:
        parts.append(f"{len(abnormal)} recent abnormal observation(s)")
    return ", ".join(parts) + ". This is an automatically assembled cognitive aid and requires clinical review."
