"""PDF extraction, Zep-safe chunking, and patient graph ingestion via graph.add(type=text)."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Callable, Literal

NoteSource = Literal["radiology_note", "session_note"]

from pypdf import PdfReader

from medtrace_agent.zep.memory import get_zep_client

DEFAULT_MAX_CHARS = 9500
PARAGRAPH_SPLIT = re.compile(r"\n\s*\n+")

# Repository root: .../src/medtrace_agent/ingest/documents.py -> parents[3]
_REPO_ROOT = Path(__file__).resolve().parents[3]


def data_note_dir(note_source: NoteSource) -> Path:
    """``data/radiology_note`` or ``data/session_note`` under the repo root."""
    return _REPO_ROOT / "data" / note_source


def ensure_data_note_dirs() -> None:
    data_note_dir("radiology_note").mkdir(parents=True, exist_ok=True)
    data_note_dir("session_note").mkdir(parents=True, exist_ok=True)


def list_txt_files_in_note_folder(note_source: NoteSource) -> list[Path]:
    """Sorted ``*.txt`` paths (UTF-8 files expected)."""
    ensure_data_note_dirs()
    d = data_note_dir(note_source)
    if not d.is_dir():
        return []
    return sorted(p for p in d.glob("*.txt") if p.is_file())


def pdf_bytes_to_text_pypdf(data: bytes) -> str:
    """Fast path: embedded text layer only (misses image-only regions)."""
    reader = PdfReader(BytesIO(data))
    parts: list[str] = []
    for page in reader.pages:
        t = page.extract_text()
        if t and t.strip():
            parts.append(t.strip())
    text = "\n\n".join(parts).strip()
    if not text:
        raise ValueError(
            "No extractable text in PDF (may be scanned/image-only). "
            "Uncheck 'Skip VLM' to use PDF → images → vision model."
        )
    return text


def pdf_bytes_to_text(
    data: bytes,
    *,
    use_vlm: bool = True,
    dpi: int | None = None,
    max_pages: int | None = None,
    progress_cb: Callable[[int, int], None] | None = None,
) -> str:
    """
    Default: render PDF pages to PNG (PyMuPDF) and extract text + structure via VLM.
    Optional ``use_vlm=False`` uses pypdf only (cheaper; may miss scans and figures).
    """
    if use_vlm:
        from medtrace_agent.ingest.scan_extract import pdf_bytes_via_vlm

        return pdf_bytes_via_vlm(
            data,
            dpi=dpi,
            max_pages=max_pages,
            progress_cb=progress_cb,
        )
    return pdf_bytes_to_text_pypdf(data)


def chunk_for_zep(
    text: str,
    max_chars: int = DEFAULT_MAX_CHARS,
    overlap: int = 250,
) -> list[str]:
    """
    Split text into chunks under Zep's graph.add ~10k limit.
    Paragraph-aware packing with hard splits and small overlap on oversized blocks.
    """
    raw = re.sub(r"[ \t]+", " ", text).strip()
    if not raw:
        return []

    paragraphs = [p.strip() for p in PARAGRAPH_SPLIT.split(raw) if p.strip()]
    if not paragraphs:
        paragraphs = [raw]

    chunks: list[str] = []
    bucket: list[str] = []
    bucket_len = 0

    def flush() -> None:
        nonlocal bucket, bucket_len
        if bucket:
            chunks.append("\n\n".join(bucket))
            bucket = []
            bucket_len = 0

    def hard_split_block(block: str) -> list[str]:
        out: list[str] = []
        start = 0
        n = len(block)
        while start < n:
            end = min(start + max_chars, n)
            piece = block[start:end]
            out.append(piece)
            if end >= n:
                break
            start = max(end - overlap, start + 1)
        return out

    for para in paragraphs:
        if len(para) > max_chars:
            flush()
            for piece in hard_split_block(para):
                chunks.append(piece)
            continue

        sep_len = 2 if bucket else 0
        if bucket_len + sep_len + len(para) <= max_chars:
            bucket.append(para)
            bucket_len += sep_len + len(para)
        else:
            flush()
            bucket.append(para)
            bucket_len = len(para)

    flush()
    return chunks


def _episode_uuid(episode: Any) -> str | None:
    """Zep SDK versions expose the episode id as ``uuid_`` or ``uuid``."""
    uid = getattr(episode, "uuid_", None) or getattr(episode, "uuid", None)
    return str(uid) if uid else None


def _ingest_text_to_patient_graph(
    user_id: str,
    text: str,
    *,
    tag: str,
    kind: str,
    filename: str,
    doc_id: str | None = None,
    extra_metadata: dict[str, Any] | None = None,
) -> list[str]:
    """Chunk ``text`` and add each chunk to the patient's Zep graph as a text episode.

    ``tag`` prefixes the per-chunk header and source description; ``kind`` lands in episode
    metadata. Both exist so PDF episodes stay distinguishable from note episodes in the graph.

    Returns episode UUIDs from the graph.add responses where available.
    """
    chunks = chunk_for_zep(text)
    if not chunks:
        return []

    client = get_zep_client()
    did = doc_id or uuid.uuid4().hex
    uploaded_at = datetime.now(timezone.utc).isoformat()
    total = len(chunks)
    episode_ids: list[str] = []

    for i, chunk in enumerate(chunks):
        header = (
            f"[{tag} doc_id={did} filename={filename} "
            f"| chunk {i + 1}/{total} | ingested_at:{uploaded_at}]\n"
        )
        meta: dict[str, Any] = {
            "kind": kind,
            "doc_id": did,
            "filename": filename,
            "chunk_index": i,
            "chunk_total": total,
            "uploaded_at": uploaded_at,
        }
        if extra_metadata:
            meta.update(extra_metadata)

        ep = client.graph.add(
            user_id=user_id,
            type="text",
            data=header + chunk,
            metadata=meta,
            source_description=f"{tag} chunk {i + 1}/{total} doc_id={did} ({filename})",
        )
        uid = _episode_uuid(ep)
        if uid:
            episode_ids.append(uid)

    return episode_ids


def ingest_pdf_text_to_patient_graph(
    user_id: str,
    text: str,
    *,
    filename: str | None = None,
    doc_id: str | None = None,
    extra_metadata: dict[str, Any] | None = None,
) -> list[str]:
    """
    Ingest plain text (already extracted from PDF) into the patient's Zep user graph.
    Each upload should pass a stable ``doc_id`` (or one is generated) for traceability in chat.
    """
    return _ingest_text_to_patient_graph(
        user_id,
        text,
        tag="ClinicalDocument",
        kind="pdf_medical_history",
        filename=filename or "unknown.pdf",
        doc_id=doc_id,
        extra_metadata=extra_metadata,
    )


def ingest_plain_text_note_to_patient_graph(
    user_id: str,
    text: str,
    *,
    note_source: NoteSource,
    filename: str,
    doc_id: str | None = None,
    extra_metadata: dict[str, Any] | None = None,
) -> list[str]:
    """
    Ingest plain text clinical notes (radiology or session) via ``chunk_for_zep`` then ``graph.add``.
    """
    return _ingest_text_to_patient_graph(
        user_id,
        text,
        tag="RadiologyNote" if note_source == "radiology_note" else "SessionNote",
        kind=note_source,
        filename=filename or "note.txt",
        doc_id=doc_id,
        extra_metadata=extra_metadata,
    )


def ingest_txt_path_to_patient_graph(
    user_id: str,
    path: Path | str,
    *,
    note_source: NoteSource,
    encoding: str = "utf-8",
    doc_id: str | None = None,
) -> list[str]:
    """Read a ``.txt`` file from disk and ingest (chunk → graph.add)."""
    allowed_dir = data_note_dir(note_source).resolve()
    p = Path(path).resolve()
    try:
        p.relative_to(allowed_dir)
    except ValueError as exc:
        raise ValueError(
            f"Note file must be under {allowed_dir}, got {p}"
        ) from exc
    text = p.read_text(encoding=encoding)
    return ingest_plain_text_note_to_patient_graph(
        user_id,
        text,
        note_source=note_source,
        filename=p.name,
        doc_id=doc_id,
        extra_metadata={"note_rel_path": str(p.relative_to(allowed_dir))},
    )
