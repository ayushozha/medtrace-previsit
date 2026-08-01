"""Patient (chart_subjects) routes."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from apps.api.dependencies import (
    RequireInsforgeDep,
    get_demo_profile_id,
)
from apps.api.schemas import (
    AbnormalFindingOut,
    AlertOut,
    AllergyOut,
    ClinicalSnapshotOut,
    ConditionOut,
    CreatePatientIn,
    LabTrendOut,
    MedicationOut,
    PatientOut,
    TimelineEvent,
)
from medtrace_agent.fireworks_config import fireworks_chat_model
from medtrace_agent.insforge_api import (
    ensure_chart_subject_id,
    fetch_documents_registry,
    get_chart_subject,
    list_chart_subjects,
    list_chat_sessions_for_chart,
    update_chart_subject_metadata,
)
from medtrace_agent.zep.memory import ensure_user, fetch_thread_context

router = APIRouter(prefix="/api/patients", tags=["patients"])


def _meta(row: dict[str, Any]) -> dict[str, Any]:
    m = row.get("metadata")
    return m if isinstance(m, dict) else {}


def _row_to_patient(row: dict[str, Any], *, document_count: int = 0) -> PatientOut:
    meta = _meta(row)
    fields: dict[str, Any] = meta.get("fields") if isinstance(meta.get("fields"), dict) else {}
    name = row.get("display_name") or fields.get("display_name") or "Unnamed patient"
    last_visit = (
        fields.get("last_visit")
        or meta.get("last_visit")
        or row.get("created_at")
        or ""
    )
    if isinstance(last_visit, str) and "T" in last_visit:
        last_visit = last_visit.split("T", 1)[0]
    return PatientOut(
        id=str(row.get("id") or ""),
        zep_user_id=str(row.get("zep_user_id") or ""),
        name=name,
        age=int(fields.get("age") or 0),
        sex=(fields.get("sex") or "O"),
        dob=fields.get("dob"),
        primary_doctor=fields.get("primary_doctor"),
        last_visit=last_visit or None,
        last_updated=row.get("created_at"),
        document_count=document_count,
        conditions=int(meta.get("condition_count") or 0),
        risk=meta.get("risk_level") or "Low",
        summary=meta.get("summary"),
        metadata=meta,
    )


@router.get("", response_model=list[PatientOut], dependencies=[RequireInsforgeDep])
def list_patients(profile_id: str = Depends(get_demo_profile_id)) -> list[PatientOut]:
    rows = list_chart_subjects(profile_id=profile_id)
    if not rows:
        return []
    docs_by_chart: dict[str, int] = {}
    all_docs = fetch_documents_registry(chart_subject_id=None)
    for d in all_docs:
        cid = d.get("chart_subject_id")
        if cid:
            docs_by_chart[str(cid)] = docs_by_chart.get(str(cid), 0) + 1
    return [
        _row_to_patient(r, document_count=docs_by_chart.get(str(r.get("id") or ""), 0))
        for r in rows
    ]


@router.post(
    "",
    response_model=PatientOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[RequireInsforgeDep],
)
def create_patient(
    body: CreatePatientIn,
    profile_id: str = Depends(get_demo_profile_id),  # noqa: ARG001
) -> PatientOut:
    if not body.zep_user_id.strip() or not body.display_name.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="zep_user_id and display_name are required.",
        )

    ensure_user(body.zep_user_id, body.display_name)

    fields: dict[str, Any] = {}
    if body.age is not None:
        fields["age"] = body.age
    if body.sex:
        fields["sex"] = body.sex
    if body.dob:
        fields["dob"] = body.dob
    if body.primary_doctor:
        fields["primary_doctor"] = body.primary_doctor
    if body.notes:
        fields["notes"] = body.notes
    if body.tags:
        fields["tags"] = body.tags

    metadata = {"fields": fields, "created_via": "react_frontend"}
    chart_id = ensure_chart_subject_id(
        zep_user_id=body.zep_user_id,
        display_name=body.display_name,
        metadata=metadata,
    )
    if not chart_id:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create chart_subject row.",
        )
    row = get_chart_subject(chart_subject_id=chart_id)
    if not row:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Created chart_subject was not retrievable.",
        )
    return _row_to_patient(row, document_count=0)


@router.get("/{chart_subject_id}", response_model=PatientOut, dependencies=[RequireInsforgeDep])
def get_patient(chart_subject_id: str) -> PatientOut:
    row = get_chart_subject(chart_subject_id=chart_subject_id)
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found.")
    docs = fetch_documents_registry(chart_subject_id=chart_subject_id)
    return _row_to_patient(row, document_count=len(docs))


@router.post(
    "/{chart_subject_id}/summary",
    response_model=PatientOut,
    dependencies=[RequireInsforgeDep],
)
def regenerate_summary(chart_subject_id: str) -> PatientOut:
    """Regenerate the AI summary using Zep memory + Fireworks; cache to metadata."""
    from medtrace_agent.agents.rag_chat import chat_with_memory

    row = get_chart_subject(chart_subject_id=chart_subject_id)
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found.")
    zep_user_id = str(row.get("zep_user_id") or "")
    sessions = list_chat_sessions_for_chart(chart_subject_id=chart_subject_id)
    if sessions:
        zep_thread_id = str(sessions[0].get("zep_thread_id") or "")
    else:
        zep_thread_id = f"summary-{chart_subject_id[:8]}-{uuid.uuid4().hex[:8]}"
        from medtrace_agent.zep.memory import ensure_session

        ensure_session(zep_thread_id, zep_user_id)
    context, msgs = fetch_thread_context(zep_thread_id)
    summary_text = chat_with_memory(
        user_input=(
            "Write a concise 3-4 sentence clinical summary of this patient based on Zep memory: "
            "active conditions, current medications, key allergies, and the most important "
            "recent change worth highlighting. Plain prose, no headings."
        ),
        user_display_name=str(row.get("display_name") or "Patient"),
        zep_context=context,
        thread_messages=msgs,
        model_name=fireworks_chat_model(),
        temperature=0.3,
    )
    update_chart_subject_metadata(
        chart_subject_id=chart_subject_id,
        metadata_patch={
            "summary": summary_text.strip(),
            "summary_generated_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    fresh = get_chart_subject(chart_subject_id=chart_subject_id) or row
    docs = fetch_documents_registry(chart_subject_id=chart_subject_id)
    return _row_to_patient(fresh, document_count=len(docs))


@router.get(
    "/{chart_subject_id}/snapshot",
    response_model=ClinicalSnapshotOut,
    dependencies=[RequireInsforgeDep],
)
def get_snapshot(chart_subject_id: str) -> ClinicalSnapshotOut:
    """Aggregated dashboard payload: a single round-trip for ``DashboardHome``."""
    from apps.api.routers.clinical import (
        _alerts,
        _allergies,
        _conditions,
        _insights,
        _labs,
        _medications,
        _recent_abnormal,
        _timeline,
    )

    row = get_chart_subject(chart_subject_id=chart_subject_id)
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found.")

    zep_user_id = str(row.get("zep_user_id") or "")
    docs_rows = fetch_documents_registry(chart_subject_id=chart_subject_id)
    from apps.api.routers.documents import _row_to_document

    documents = [_row_to_document(r) for r in docs_rows]
    patient = _row_to_patient(row, document_count=len(documents))
    meta = _meta(row)
    clinical = meta.get("clinical") if isinstance(meta.get("clinical"), dict) else {}

    # Local-mock (and any seed that embeds clinical fixtures) prefers metadata
    # so the dashboard works without Zep graph content.
    if clinical:
        return ClinicalSnapshotOut(
            patient=patient,
            insights=_insights(zep_user_id, meta),
            active_conditions=[
                ConditionOut.model_validate(c)
                for c in clinical.get("conditions") or []
                if isinstance(c, dict)
            ],
            current_medications=[
                MedicationOut.model_validate(c)
                for c in clinical.get("medications") or []
                if isinstance(c, dict)
            ],
            allergies=[
                AllergyOut.model_validate(c)
                for c in clinical.get("allergies") or []
                if isinstance(c, dict)
            ],
            recent_abnormal=[
                AbnormalFindingOut.model_validate(c)
                for c in clinical.get("recent_abnormal") or []
                if isinstance(c, dict)
            ],
            risk_alerts=[
                AlertOut.model_validate(c)
                for c in clinical.get("alerts") or []
                if isinstance(c, dict)
            ],
            lab_trends=[
                LabTrendOut.model_validate(c)
                for c in clinical.get("labs") or []
                if isinstance(c, dict)
            ],
            timeline=[
                TimelineEvent.model_validate(c)
                for c in clinical.get("timeline") or []
                if isinstance(c, dict)
            ],
            documents=documents,
            doctor_checklist=meta.get("doctor_checklist")
            or [
                "Review elevated HbA1c trend",
                "Confirm medication adherence",
                "Check documented allergies before prescribing antibiotics",
            ],
        )

    return ClinicalSnapshotOut(
        patient=patient,
        insights=_insights(zep_user_id, meta),
        active_conditions=_conditions(zep_user_id),
        current_medications=_medications(zep_user_id),
        allergies=_allergies(zep_user_id),
        recent_abnormal=_recent_abnormal(zep_user_id),
        risk_alerts=_alerts(zep_user_id),
        lab_trends=_labs(zep_user_id),
        timeline=_timeline(zep_user_id),
        documents=documents,
        doctor_checklist=meta.get("doctor_checklist")
        or [
            "Review elevated HbA1c trend",
            "Confirm medication adherence",
            "Check documented allergies before prescribing antibiotics",
        ],
    )
