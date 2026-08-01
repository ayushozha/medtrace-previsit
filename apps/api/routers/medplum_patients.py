"""Medplum-backed patient directory and aggregated dashboard routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from apps.api.dependencies import RequireMedplumDep
from apps.api.routers.medplum_common import deterministic_summary, raise_medplum_http
from apps.api.schemas import (
    AbnormalFindingOut,
    AlertOut,
    AllergyOut,
    ClinicalSnapshotOut,
    ChecklistItemOut,
    ConditionOut,
    CreatePatientIn,
    DocumentOut,
    InsightOut,
    LabTrendOut,
    MedicationOut,
    PatientOut,
    TimelineEvent,
    UpdateChecklistItemIn,
    UpdatePatientIn,
)
from medtrace_agent.medplum import MedplumError
from medtrace_agent.medplum_repository import repository


router = APIRouter(prefix="/api/patients", tags=["patients"])


def _patient_or_404(patient_id: str) -> dict:
    try:
        patient = repository().get_patient(patient_id)
    except MedplumError as exc:
        raise_medplum_http(exc)
    if not patient:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found.")
    return patient


def _view_with_summary(patient: dict, resources: dict) -> PatientOut:
    repo = repository()
    documents = repo.list_documents(str(patient["id"]), resources)
    view = repo.patient_view(patient, document_count=len(documents), clinical=resources)
    conditions = repo.condition_views(resources)
    medications = repo.medication_views(resources)
    allergies = repo.allergy_views(resources)
    labs = repo.lab_views(resources)
    view["summary"] = deterministic_summary(view["name"], conditions, medications, allergies, labs)
    return PatientOut.model_validate(view)


@router.get("", response_model=list[PatientOut], dependencies=[RequireMedplumDep])
def list_patients() -> list[PatientOut]:
    repo = repository()
    try:
        out = []
        for patient in repo.list_patients():
            resources = repo.clinical_resources(str(patient["id"]))
            out.append(_view_with_summary(patient, resources))
        return out
    except MedplumError as exc:
        raise_medplum_http(exc)


@router.post("", response_model=PatientOut, status_code=status.HTTP_201_CREATED, dependencies=[RequireMedplumDep])
def create_patient(body: CreatePatientIn) -> PatientOut:
    if not body.zep_user_id.strip() or not body.display_name.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="zep_user_id and display_name are required.")
    repo = repository()
    try:
        patient = repo.upsert_patient(
            zep_user_id=body.zep_user_id.strip(),
            display_name=body.display_name.strip(),
            dob=body.dob,
            age=body.age,
            sex=body.sex,
            primary_doctor=body.primary_doctor,
            tags=body.tags,
        )
        resources = repo.clinical_resources(str(patient["id"]))
        return _view_with_summary(patient, resources)
    except MedplumError as exc:
        raise_medplum_http(exc)


@router.get("/{patient_id}", response_model=PatientOut, dependencies=[RequireMedplumDep])
def get_patient(patient_id: str) -> PatientOut:
    patient = _patient_or_404(patient_id)
    try:
        return _view_with_summary(patient, repository().clinical_resources(patient_id))
    except MedplumError as exc:
        raise_medplum_http(exc)


@router.patch("/{patient_id}", response_model=PatientOut, dependencies=[RequireMedplumDep])
def update_patient(patient_id: str, body: UpdatePatientIn) -> PatientOut:
    updates = body.model_dump(exclude_unset=True)
    if "display_name" in updates and not str(updates["display_name"] or "").strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="display_name cannot be empty.")
    repo = repository()
    try:
        patient = repo.update_patient(patient_id, updates)
        if not patient:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found.")
        return _view_with_summary(patient, repo.clinical_resources(patient_id))
    except MedplumError as exc:
        raise_medplum_http(exc)


@router.post("/{patient_id}/summary", response_model=PatientOut, dependencies=[RequireMedplumDep])
def regenerate_summary(patient_id: str) -> PatientOut:
    # Summaries are response-only cognitive aids; no AI prose is written back to FHIR.
    return get_patient(patient_id)


@router.get("/{patient_id}/snapshot", response_model=ClinicalSnapshotOut, dependencies=[RequireMedplumDep])
def get_snapshot(patient_id: str) -> ClinicalSnapshotOut:
    patient_resource = _patient_or_404(patient_id)
    repo = repository()
    try:
        resources = repo.clinical_resources(patient_id)
        patient = _view_with_summary(patient_resource, resources)
        conditions = [ConditionOut.model_validate(item) for item in repo.condition_views(resources)]
        medications = [MedicationOut.model_validate(item) for item in repo.medication_views(resources)]
        allergies = [AllergyOut.model_validate(item) for item in repo.allergy_views(resources)]
        labs = [LabTrendOut.model_validate(item) for item in repo.lab_views(resources)]
        abnormal = [AbnormalFindingOut.model_validate(item) for item in repo.abnormal_views(resources)]
        alerts = [AlertOut.model_validate(item) for item in repo.alert_views(resources)]
        insights = [InsightOut.model_validate(item) for item in repo.insight_views(resources)]
        timeline = [TimelineEvent.model_validate(item) for item in repo.timeline_views(resources)]
        documents = [DocumentOut.model_validate(item) for item in repo.list_documents(patient_id, resources)]
        checklist = [f"Review: {item.message}" for item in alerts[:3]] or [
            "Review automatically extracted records before clinical use",
            "Confirm medication and allergy history with the patient",
        ]
        checklist_items = repo.checklist_views(
            patient_id=patient_id,
            resources=resources,
            suggestions=checklist,
        )
        return ClinicalSnapshotOut(
            patient=patient,
            insights=insights,
            active_conditions=conditions,
            current_medications=medications,
            allergies=allergies,
            recent_abnormal=abnormal,
            risk_alerts=alerts,
            lab_trends=labs,
            timeline=timeline,
            documents=documents,
            doctor_checklist=checklist,
            doctor_checklist_items=[ChecklistItemOut.model_validate(item) for item in checklist_items],
        )
    except MedplumError as exc:
        raise_medplum_http(exc)


@router.patch(
    "/{patient_id}/checklist/{item_id}",
    response_model=ChecklistItemOut,
    dependencies=[RequireMedplumDep],
)
def update_checklist_item(
    patient_id: str,
    item_id: str,
    body: UpdateChecklistItemIn,
) -> ChecklistItemOut:
    _patient_or_404(patient_id)
    try:
        task = repository().upsert_checklist_item(
            patient_id=patient_id,
            item_id=item_id,
            text=body.text,
            done=body.done,
            agent_note=body.agent_note,
        )
        return ChecklistItemOut(
            id=item_id,
            text=str(task.get("description") or body.text),
            done=task.get("status") == "completed",
            agent_note=str((task.get("businessStatus") or {}).get("text") or "") or None,
        )
    except MedplumError as exc:
        raise_medplum_http(exc)
