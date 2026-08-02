"""Patient-linked DICOM, segmentation, and draft-report routes.

Medplum owns the canonical ImagingStudy/DiagnosticReport metadata and a patient-scoped
representative DICOM Binary.  The complete series stays in the local demo imaging store
used by the browser viewer.
"""

from __future__ import annotations

from pathlib import Path
from shutil import rmtree
from time import time_ns
import uuid

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from apps.api.dependencies import RequireMedplumDep
from apps.api.demo_security import DemoOperatorDep
from apps.api.routers.medplum_common import (
    is_synthetic_patient,
    raise_medplum_http,
    require_synthetic_patient,
)
from apps.api.schemas import (
    ReportOut,
    ReportRequest,
    ReportReviewIn,
    ReportReviewOut,
    SegmentationOut,
    SegmentationRequest,
    StudyOut,
)
from medtrace_agent.imaging import (
    MedGemmaService,
    MedSAM2Service,
    is_dicom_file,
    render_dicom_preview,
    study_dir,
)
from medtrace_agent.imaging.series import has_volume_geometry, sort_series
from medtrace_agent.imaging.storage import study_dicom_url, study_preview_url
from medtrace_agent.medplum import MedplumError
from medtrace_agent.medplum_repository import IMAGING_STUDY_SYSTEM, identifier_value, repository

router = APIRouter(prefix="/api/studies", tags=["imaging"])
_MAX_DICOM_FILES = 2_000
_MAX_DICOM_BYTES = 512 * 1024 * 1024

medsam2_service = MedSAM2Service()
medgemma_service = MedGemmaService()


def _patient_id(study: dict) -> str:
    reference = str((study.get("subject") or {}).get("reference") or "")
    return reference.removeprefix("Patient/") if reference.startswith("Patient/") else ""


def _local_dicoms(study_id: str) -> list[Path]:
    directory = study_dir(study_id)
    if not directory.is_dir():
        return []
    return [path for path in directory.iterdir() if path.is_file() and is_dicom_file(path)]


def _synthetic_study_or_404(study_id: str) -> dict:
    repo = repository()
    study = repo.client.search_one(
        "ImagingStudy",
        {"identifier": f"{IMAGING_STUDY_SYSTEM}|{study_id}"},
    )
    if not study:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Imaging study not found.")
    require_synthetic_patient(repo.get_patient(_patient_id(study)))
    return study


def _trusted_report_review(repo, report: dict) -> tuple[str, str | None]:
    decision, note = repo.report_review(report)
    if decision == "unreviewed":
        return decision, note
    reviewer = repo.report_reviewer(report)
    return (decision, note) if reviewer.get("reviewer_id") and reviewer.get("reviewer_name") else ("unreviewed", None)


def _study_out(study: dict) -> StudyOut:
    repo = repository()
    study_id = identifier_value(study, IMAGING_STUDY_SYSTEM) or str(study.get("id") or "")
    patient_id = _patient_id(study)
    series_rows = [row for row in study.get("series") or [] if isinstance(row, dict)]
    first_series = series_rows[0] if series_rows else {}
    modality_coding = first_series.get("modality") if isinstance(first_series, dict) else {}
    body_coding = first_series.get("bodySite") if isinstance(first_series, dict) else {}
    local = sort_series(_local_dicoms(study_id))
    first_path = local[0].path if local else None
    document = repo.imaging_document(study_id)
    report_resource = repo.diagnostic_report_for_study(study_id)
    review_decision, review_note = (
        _trusted_report_review(repo, report_resource) if report_resource else ("unreviewed", None)
    )
    report = repo.diagnostic_report_view(report_resource) if report_resource else None
    return StudyOut(
        id=study_id,
        patient_id=patient_id,
        fhir_imaging_study_id=str(study.get("id") or ""),
        fhir_document_reference_id=str(document.get("id") or "") if document else None,
        patient_name=str((study.get("subject") or {}).get("display") or "Medplum patient"),
        patient_detail=f"Patient/{patient_id}",
        modality=str((modality_coding or {}).get("code") or "DICOM"),
        body_part=str((body_coding or {}).get("display") or (body_coding or {}).get("code") or "Unspecified"),
        series=str(first_series.get("description") or study.get("description") or "DICOM series"),
        slices=int(study.get("numberOfInstances") or len(local) or 1),
        uploaded_file_name=(first_path.name if len(local) == 1 and first_path else f"{len(local)} local slices" if local else "Medplum DICOM"),
        preview_url=study_preview_url(study_id) if (study_dir(study_id) / "preview.png").is_file() else None,
        dicom_url=study_dicom_url(study_id, first_path.name) if first_path else None,
        slice_urls=[study_dicom_url(study_id, item.path.name) for item in local],
        has_volume_geometry=has_volume_geometry([item.path for item in local]),
        uploaded_at=str(study.get("started") or (study.get("meta") or {}).get("lastUpdated") or "") or None,
        review_decision=review_decision,
        review_note=review_note,
        report=ReportOut.model_validate(report) if report else None,
    )


@router.get("", response_model=list[StudyOut], dependencies=[RequireMedplumDep])
def list_studies(patient_id: str | None = None) -> list[StudyOut]:
    try:
        repo = repository()
        if patient_id:
            require_synthetic_patient(repo.get_patient(patient_id))
        return [
            _study_out(study)
            for study in repo.list_imaging_studies(patient_id)
            if is_synthetic_patient(repo.get_patient(_patient_id(study)))
        ]
    except MedplumError as exc:
        raise_medplum_http(exc)


@router.post(
    "",
    response_model=StudyOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[RequireMedplumDep],
)
async def create_study(
    patient_id: str = Form(...),
    files: list[UploadFile] = File(...),
) -> StudyOut:
    """Store one DICOM series and register its canonical Patient relationship."""
    if not files:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No files were uploaded.")
    if len(files) > _MAX_DICOM_FILES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Too many DICOM files.")

    repo = repository()
    try:
        require_synthetic_patient(repo.get_patient(patient_id))
    except MedplumError as exc:
        raise_medplum_http(exc)

    study_id = f"ST-{time_ns()}-{uuid.uuid4().hex[:6]}"
    target_dir = study_dir(study_id)
    target_dir.mkdir(parents=True, exist_ok=True)

    stored: list[Path] = []
    total_bytes = 0
    for index, upload in enumerate(files):
        leaf = Path(upload.filename or f"slice-{index:05d}.dcm").name
        path = target_dir / leaf
        if path.exists():
            path = target_dir / f"{index:05d}-{leaf}"
        with path.open("wb") as output:
            while chunk := upload.file.read(1024 * 1024):
                total_bytes += len(chunk)
                if total_bytes > _MAX_DICOM_BYTES:
                    rmtree(target_dir, ignore_errors=True)
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail="DICOM upload exceeds 512 MiB.",
                    )
                output.write(chunk)
        if is_dicom_file(path):
            stored.append(path)
        else:
            path.unlink(missing_ok=True)

    if not stored:
        rmtree(target_dir, ignore_errors=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No valid DICOM files found. Upload .dcm files or a study folder.",
        )

    ordered = sort_series(stored)
    first = ordered[0].path
    metadata = render_dicom_preview(first, target_dir)
    if len(ordered) > 1:
        metadata["slices"] = len(ordered)

    try:
        imaging_study, _document, _source = repo.upsert_imaging_study(
            patient_id=patient_id,
            study_id=study_id,
            paths=[item.path for item in ordered],
        )
        return _study_out(imaging_study)
    except MedplumError as exc:
        rmtree(target_dir, ignore_errors=True)
        raise_medplum_http(exc)
    except ValueError as exc:
        rmtree(target_dir, ignore_errors=True)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post(
    "/{study_id}/segmentations/medsam2",
    response_model=SegmentationOut,
    dependencies=[RequireMedplumDep],
)
def segment_with_medsam2(study_id: str, request: SegmentationRequest) -> SegmentationOut:
    _synthetic_study_or_404(study_id)
    if not (medsam2_service.endpoint or medsam2_service.adapter_module):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MedSAM2 is not configured; synthetic segmentation is disabled.",
        )
    result = medsam2_service.segment(study_id=study_id, prompt=request.prompt)
    if result.get("source") == "mock":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MedSAM2 is not configured; synthetic segmentation is disabled.",
        )
    result.setdefault("box", request.prompt.model_dump())
    return SegmentationOut(**result)


@router.post(
    "/{study_id}/reports/qwen-vl",
    response_model=ReportOut,
    dependencies=[RequireMedplumDep],
)
def report_with_qwen_vl(study_id: str, request: ReportRequest) -> ReportOut:
    try:
        study = _synthetic_study_or_404(study_id)
        if medgemma_service.status()["provider"] == "mock":
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Imaging report provider is not configured; synthetic reports are disabled.",
            )
        result = medgemma_service.generate_report(study_id=study_id, request=request)
        if result.get("source") == "mock":
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Imaging report provider is not configured; synthetic reports are disabled.",
            )
        persisted = repository().upsert_diagnostic_report(
            patient_id=_patient_id(study),
            study_id=study_id,
            report=result,
        )
        return ReportOut(**result, fhir_diagnostic_report_id=str(persisted.get("id") or ""))
    except HTTPException:
        raise
    except MedplumError as exc:
        raise_medplum_http(exc)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — upstream provider failure
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Report generation failed: {exc}",
        ) from exc


@router.post(
    "/{study_id}/reports/medgemma",
    response_model=ReportOut,
    dependencies=[RequireMedplumDep],
)
def report_with_medgemma(study_id: str, request: ReportRequest) -> ReportOut:
    """Alias kept for the MedGemma adapter path; resolves the same provider ladder."""
    return report_with_qwen_vl(study_id, request)


@router.post(
    "/{study_id}/reports/review",
    response_model=ReportReviewOut,
    dependencies=[RequireMedplumDep],
)
def review_report(
    study_id: str,
    request: ReportReviewIn,
    operator: DemoOperatorDep,
) -> ReportReviewOut:
    try:
        study = _synthetic_study_or_404(study_id)
        report, task = repository().review_diagnostic_report(
            patient_id=_patient_id(study),
            study_id=study_id,
            decision=request.decision,
            note=request.note,
            reviewer_id=operator.operator_id,
            reviewer_name=operator.display_name,
        )
        return ReportReviewOut(
            decision=request.decision,
            note=request.note,
            fhir_diagnostic_report_id=str(report.get("id") or ""),
            fhir_task_id=str(task.get("id") or ""),
        )
    except HTTPException:
        raise
    except MedplumError as exc:
        raise_medplum_http(exc)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
