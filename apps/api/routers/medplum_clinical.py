"""FHIR-derived clinical subresource routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from apps.api.dependencies import RequireMedplumDep
from apps.api.routers.medplum_common import raise_medplum_http, require_synthetic_patient
from apps.api.schemas import (
    AbnormalFindingOut,
    AlertOut,
    AllergyOut,
    ConditionOut,
    InsightOut,
    LabTrendOut,
    MedicationOut,
    TimelineEvent,
)
from medtrace_agent.medplum import MedplumError
from medtrace_agent.medplum_repository import repository


router = APIRouter(prefix="/api/patients", tags=["clinical"])


def _resources(patient_id: str) -> dict:
    repo = repository()
    try:
        require_synthetic_patient(repo.get_patient(patient_id))
        return repo.clinical_resources(patient_id)
    except MedplumError as exc:
        raise_medplum_http(exc)


@router.get("/{patient_id}/timeline", response_model=list[TimelineEvent], dependencies=[RequireMedplumDep])
def get_timeline(patient_id: str) -> list[TimelineEvent]:
    return [TimelineEvent.model_validate(item) for item in repository().timeline_views(_resources(patient_id))]


@router.get("/{patient_id}/labs", response_model=list[LabTrendOut], dependencies=[RequireMedplumDep])
def get_labs(patient_id: str) -> list[LabTrendOut]:
    return [LabTrendOut.model_validate(item) for item in repository().lab_views(_resources(patient_id))]


@router.get("/{patient_id}/conditions", response_model=list[ConditionOut], dependencies=[RequireMedplumDep])
def get_conditions(patient_id: str) -> list[ConditionOut]:
    return [ConditionOut.model_validate(item) for item in repository().condition_views(_resources(patient_id))]


@router.get("/{patient_id}/medications", response_model=list[MedicationOut], dependencies=[RequireMedplumDep])
def get_medications(patient_id: str) -> list[MedicationOut]:
    return [MedicationOut.model_validate(item) for item in repository().medication_views(_resources(patient_id))]


@router.get("/{patient_id}/allergies", response_model=list[AllergyOut], dependencies=[RequireMedplumDep])
def get_allergies(patient_id: str) -> list[AllergyOut]:
    return [AllergyOut.model_validate(item) for item in repository().allergy_views(_resources(patient_id))]


@router.get("/{patient_id}/abnormal", response_model=list[AbnormalFindingOut], dependencies=[RequireMedplumDep])
def get_abnormal(patient_id: str) -> list[AbnormalFindingOut]:
    return [AbnormalFindingOut.model_validate(item) for item in repository().abnormal_views(_resources(patient_id))]


@router.get("/{patient_id}/alerts", response_model=list[AlertOut], dependencies=[RequireMedplumDep])
def get_alerts(patient_id: str) -> list[AlertOut]:
    return [AlertOut.model_validate(item) for item in repository().alert_views(_resources(patient_id))]


@router.get("/{patient_id}/insights", response_model=list[InsightOut], dependencies=[RequireMedplumDep])
def get_insights(patient_id: str) -> list[InsightOut]:
    return [InsightOut.model_validate(item) for item in repository().insight_views(_resources(patient_id))]
