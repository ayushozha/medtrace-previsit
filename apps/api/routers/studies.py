"""Imaging routes: DICOM study upload, MedSAM2 segmentation, draft report generation.

Independent of the clinical routes — no InsForge or Zep involvement. Studies are files
under ``data/studies/`` (see ``medtrace_agent.imaging.storage``), served at ``/data``.
Every model call degrades to deterministic mock output when no provider is configured,
so these routes work with no secrets at all.
"""

from __future__ import annotations

from pathlib import Path
from shutil import copyfileobj, rmtree
from time import time

from fastapi import APIRouter, File, HTTPException, UploadFile, status

from apps.api.schemas import (
    ReportOut,
    ReportRequest,
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

router = APIRouter(prefix="/api/studies", tags=["imaging"])

medsam2_service = MedSAM2Service()
medgemma_service = MedGemmaService()


@router.post("", response_model=StudyOut, status_code=status.HTTP_201_CREATED)
async def create_study(files: list[UploadFile] = File(...)) -> StudyOut:
    """Store an uploaded DICOM study and render its preview PNG.

    Accepts either one file (single image, or a multi-frame volume) or a whole series —
    a real CT/MR study is a folder of single-frame files, one per slice. Series are sorted
    into anatomical order before being exposed, because upload order is arbitrary.
    """
    if not files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No files were uploaded."
        )

    study_id = f"ST-{int(time())}"
    target_dir = study_dir(study_id)
    target_dir.mkdir(parents=True, exist_ok=True)

    stored: list[Path] = []
    for index, upload in enumerate(files):
        # Browsers send directory uploads with a relative path; keep only the leaf, and
        # keep names unique so two folders with slice_0001.dcm cannot collide.
        leaf = Path(upload.filename or f"slice-{index:05d}.dcm").name
        path = target_dir / leaf
        if path.exists():
            path = target_dir / f"{index:05d}-{leaf}"
        with path.open("wb") as output:
            copyfileobj(upload.file, output)
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

    # A multi-file series overrides the per-file frame count: the volume is the stack.
    if len(ordered) > 1:
        metadata["slices"] = len(ordered)

    return StudyOut(
        id=study_id,
        uploaded_file_name=first.name if len(ordered) == 1 else f"{len(ordered)} slices",
        preview_url=study_preview_url(study_id),
        dicom_url=study_dicom_url(study_id, first.name),
        slice_urls=[study_dicom_url(study_id, s.path.name) for s in ordered],
        has_volume_geometry=has_volume_geometry([s.path for s in ordered]),
        **metadata,
    )


@router.post("/{study_id}/segmentations/medsam2", response_model=SegmentationOut)
def segment_with_medsam2(study_id: str, request: SegmentationRequest) -> SegmentationOut:
    result = medsam2_service.segment(study_id=study_id, prompt=request.prompt)
    result.setdefault("box", request.prompt.model_dump())
    return SegmentationOut(**result)


@router.post("/{study_id}/reports/qwen-vl", response_model=ReportOut)
def report_with_qwen_vl(study_id: str, request: ReportRequest) -> ReportOut:
    try:
        result = medgemma_service.generate_report(study_id=study_id, request=request)
    except Exception as exc:  # noqa: BLE001 — upstream provider failure
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Report generation failed: {exc}",
        ) from exc
    return ReportOut(**result)


@router.post("/{study_id}/reports/medgemma", response_model=ReportOut)
def report_with_medgemma(study_id: str, request: ReportRequest) -> ReportOut:
    """Alias kept for the MedGemma adapter path; resolves the same provider ladder."""
    return report_with_qwen_vl(study_id, request)
