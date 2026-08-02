"""Canonical patient consultation persistence."""

from __future__ import annotations

import base64
import binascii

from fastapi import APIRouter, HTTPException, status

from apps.api.dependencies import RequireMedplumDep
from apps.api.routers.medplum_common import raise_medplum_http, require_synthetic_patient
from apps.api.schemas import ConsultationIn, ConsultationOut, ConsultationSessionOut
from medtrace_agent.medplum import MedplumError
from medtrace_agent.medplum_repository import repository


router = APIRouter(prefix="/api/patients", tags=["consultations"])


@router.get(
    "/{patient_id}/consultations",
    response_model=list[ConsultationSessionOut],
    dependencies=[RequireMedplumDep],
)
def list_consultations(patient_id: str) -> list[ConsultationSessionOut]:
    repo = repository()
    try:
        require_synthetic_patient(repo.get_patient(patient_id))
        return [
            ConsultationSessionOut.model_validate(item)
            for item in repo.consultation_views(patient_id)
        ]
    except MedplumError as exc:
        raise_medplum_http(exc)


@router.post(
    "/{patient_id}/consultations",
    response_model=ConsultationOut,
    dependencies=[RequireMedplumDep],
)
def upsert_consultation(patient_id: str, body: ConsultationIn) -> ConsultationOut:
    repo = repository()
    try:
        require_synthetic_patient(repo.get_patient(patient_id))
    except MedplumError as exc:
        raise_medplum_http(exc)
    audio: bytes | None = None
    content_type = body.audio_content_type
    if body.audio_base64:
        encoded = body.audio_base64
        if encoded.startswith("data:") and "," in encoded:
            header, encoded = encoded.split(",", 1)
            declared = header.removeprefix("data:").split(";", 1)[0]
            if declared:
                content_type = declared
        try:
            audio = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="audio_base64 is not valid base64 data.",
            ) from exc

    try:
        result = repo.upsert_consultation(
            patient_id=patient_id,
            consultation_id=body.consultation_id,
            transcript=body.transcript,
            report=body.report,
            duration=body.duration,
            recorded_at=body.recorded_at,
            audio=audio,
            audio_content_type=content_type,
        )
        return ConsultationOut(
            consultation_id=body.consultation_id,
            patient_id=patient_id,
            encounter_id=result["encounter_id"],
            document_ids=result["document_ids"],
        )
    except MedplumError as exc:
        raise_medplum_http(exc)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
