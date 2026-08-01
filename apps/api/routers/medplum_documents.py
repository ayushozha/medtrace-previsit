"""Medplum Binary/DocumentReference upload and extraction routes."""

from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from apps.api.dependencies import RequireMedplumDep
from apps.api.routers.medplum_common import raise_medplum_http, safe_processing_error
from apps.api.schemas import DocumentKind, DocumentOut, IngestResult
from medtrace_agent.fireworks_config import fireworks_vlm_model
from medtrace_agent.ingest.documents import (
    ingest_pdf_text_to_patient_graph,
    ingest_plain_text_note_to_patient_graph,
    pdf_bytes_to_text_pypdf,
)
from medtrace_agent.medplum import MedplumError
from medtrace_agent.medplum_extraction import facts_from_plain_text, facts_from_vlm_pages
from medtrace_agent.medplum_repository import repository
from medtrace_agent.zep.memory import ensure_user


router = APIRouter(tags=["documents"])


def _patient_or_404(patient_id: str) -> dict:
    try:
        patient = repository().get_patient(patient_id)
    except MedplumError as exc:
        raise_medplum_http(exc)
    if not patient:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found.")
    return patient


@router.get("/api/patients/{patient_id}/documents", response_model=list[DocumentOut], dependencies=[RequireMedplumDep])
def list_documents(patient_id: str) -> list[DocumentOut]:
    _patient_or_404(patient_id)
    try:
        return [DocumentOut.model_validate(item) for item in repository().list_documents(patient_id)]
    except MedplumError as exc:
        raise_medplum_http(exc)


@router.post(
    "/api/patients/{patient_id}/documents",
    response_model=IngestResult,
    status_code=status.HTTP_201_CREATED,
    dependencies=[RequireMedplumDep],
)
async def upload_document(
    patient_id: str,
    file: UploadFile = File(...),
    document_kind: DocumentKind = Form("clinical_pdf"),
    extract_mode: str = Form("vlm_png"),
    dpi: int | None = Form(None),
    max_pages: int | None = Form(None),
) -> IngestResult:
    patient = _patient_or_404(patient_id)
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty upload.")
    filename = file.filename or "upload.bin"
    content_type = file.content_type or ("application/pdf" if document_kind == "clinical_pdf" else "text/plain")
    repo = repository()
    try:
        doc, task = repo.create_document(
            patient_id=patient_id,
            data=raw,
            filename=filename,
            content_type=content_type,
            document_kind=document_kind,
            extract_mode=extract_mode,
        )
    except MedplumError as exc:
        raise_medplum_http(exc)

    episode_ids: list[str] = []
    try:
        if document_kind == "clinical_pdf" and extract_mode != "pypdf":
            from medtrace_agent.ingest.scan_extract import pdf_bytes_via_vlm_structured

            pages, text = pdf_bytes_via_vlm_structured(raw, dpi=dpi, max_pages=max_pages)
            facts = facts_from_vlm_pages(pages)
            model_name = fireworks_vlm_model()
        elif document_kind == "clinical_pdf":
            text = pdf_bytes_to_text_pypdf(raw)
            facts = facts_from_plain_text(text)
            model_name = "pypdf-embedded-text"
        else:
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError("Notes must be UTF-8 text.") from exc
            facts = facts_from_plain_text(text)
            model_name = "plain-text-patterns"

        doc, task = repo.attach_extracted_text(doc, task, text)
        repo.create_extracted_facts(
            patient_id=patient_id,
            doc_id=str(doc["id"]),
            facts=facts,
            model_name=model_name,
        )

        zep_user_id = next(
            (str(identifier.get("value")) for identifier in patient.get("identifier") or [] if identifier.get("system", "").endswith("/zep-user")),
            "",
        )
        ensure_user(zep_user_id, str((patient.get("name") or [{}])[0].get("text") or zep_user_id))
        if document_kind == "clinical_pdf":
            episode_ids = ingest_pdf_text_to_patient_graph(zep_user_id, text, filename=filename, doc_id=str(doc["id"]))
        else:
            episode_ids = ingest_plain_text_note_to_patient_graph(
                zep_user_id,
                text,
                note_source="radiology_note" if document_kind == "radiology_note" else "session_note",
                filename=filename,
                doc_id=str(doc["id"]),
            )
        task = repo.finish_task(task, episode_count=len(episode_ids))
    except Exception as exc:
        try:
            task = repo.finish_task(task, error=safe_processing_error(exc))
        except MedplumError:
            pass

    return IngestResult(document=DocumentOut.model_validate(repo.document_view(doc, [task])), episode_ids=episode_ids)
