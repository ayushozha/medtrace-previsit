"""Document upload + registry routes."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from apps.api.dependencies import RequireInsforgeDep
from apps.api.schemas import DocumentKind, DocumentOut, IngestResult
from medtrace_agent.ingest.documents import (
    ingest_pdf_text_to_patient_graph,
    ingest_plain_text_note_to_patient_graph,
    pdf_bytes_to_text,
)
from medtrace_agent.insforge_api import (
    document_row_to_ingested_doc,
    documents_bucket,
    fetch_documents_registry,
    get_chart_subject,
    insert_document_record,
    upload_bytes_to_bucket,
)

router = APIRouter(tags=["documents"])

_KIND_TO_NOTE_SOURCE: dict[str, str] = {
    "radiology_note": "radiology_note",
    "conversation_note": "session_note",
}


def _row_to_document(row: dict[str, Any]) -> DocumentOut:
    shape = document_row_to_ingested_doc(row)
    return DocumentOut(
        doc_id=str(shape.get("doc_id") or ""),
        filename=str(shape.get("filename") or "file"),
        document_kind=str(shape.get("document_kind") or "clinical_pdf"),  # type: ignore[arg-type]
        extract_mode=row.get("extract_mode"),
        episode_count=int(shape.get("episode_count") or 0),
        storage_url=shape.get("storage_url"),
        storage_key=shape.get("storage_key"),
        storage_bucket=row.get("storage_bucket"),
        uploaded_at=str(shape.get("uploaded_at_utc") or row.get("uploaded_at") or ""),
        status="Processed",
        review_status="Needs review" if not (row.get("episode_count") or 0) else "Approved",
    )


@router.get(
    "/api/patients/{chart_subject_id}/documents",
    response_model=list[DocumentOut],
    dependencies=[RequireInsforgeDep],
)
def list_documents(chart_subject_id: str) -> list[DocumentOut]:
    if not get_chart_subject(chart_subject_id=chart_subject_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found.")
    rows = fetch_documents_registry(chart_subject_id=chart_subject_id)
    return [_row_to_document(r) for r in rows]


@router.post(
    "/api/patients/{chart_subject_id}/documents",
    response_model=IngestResult,
    status_code=status.HTTP_201_CREATED,
    dependencies=[RequireInsforgeDep],
)
async def upload_document(
    chart_subject_id: str,
    file: UploadFile = File(...),
    document_kind: DocumentKind = Form("clinical_pdf"),
    extract_mode: str = Form("vlm_png"),
    dpi: int | None = Form(None),
    max_pages: int | None = Form(None),
) -> IngestResult:
    """Ingest one document into the patient's Zep graph and the document registry.

    ``extract_mode="pypdf"`` reads the PDF text layer only (cheap, misses scans); anything
    else renders each page to PNG and calls the VLM. ``dpi`` and ``max_pages`` tune that
    vision path and fall back to ``PDF_VL_DPI`` / ``PDF_VL_MAX_PAGES`` when unset — one
    multimodal call is billed per page, so cap large PDFs.
    """
    chart = get_chart_subject(chart_subject_id=chart_subject_id)
    if not chart:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found.")
    zep_user_id = str(chart.get("zep_user_id") or "")
    if not zep_user_id:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="chart_subject is missing zep_user_id.",
        )

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty upload.")

    filename = file.filename or "upload.bin"
    doc_id = uuid.uuid4().hex

    text: str
    if document_kind == "clinical_pdf":
        use_vlm = extract_mode != "pypdf"
        try:
            text = pdf_bytes_to_text(raw, use_vlm=use_vlm, dpi=dpi, max_pages=max_pages)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"PDF text extraction failed: {exc}",
            ) from exc
        episode_ids = ingest_pdf_text_to_patient_graph(
            zep_user_id,
            text,
            filename=filename,
            doc_id=doc_id,
        )
    else:
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Notes must be UTF-8 text.",
            ) from exc
        note_source = _KIND_TO_NOTE_SOURCE.get(document_kind, "session_note")
        episode_ids = ingest_plain_text_note_to_patient_graph(
            zep_user_id,
            text,
            note_source=note_source,  # type: ignore[arg-type]
            filename=filename,
            doc_id=doc_id,
        )

    up = upload_bytes_to_bucket(raw, filename, content_type=file.content_type)
    bucket = up.get("bucket") or documents_bucket()
    key = up.get("key")
    if not key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Storage upload missing key in response.",
        )
    inserted = insert_document_record(
        doc_id=doc_id,
        filename=filename,
        document_kind=document_kind,
        storage_bucket=str(bucket),
        storage_key=str(key),
        storage_url=str(up.get("url") or "") or None,
        chart_subject_id=chart_subject_id,
        extract_mode=extract_mode,
        episode_count=len(episode_ids),
        metadata={"content_type": file.content_type or ""},
    )
    if not inserted:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to write documents row.",
        )
    return IngestResult(
        document=_row_to_document(inserted),
        episode_ids=episode_ids,
    )
