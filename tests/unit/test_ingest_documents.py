"""Unit tests for ``medtrace_agent.ingest.documents``."""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import MagicMock, patch

import fitz
import pytest
from pypdf import PdfWriter

from medtrace_agent.ingest import documents as doc


def test_chunk_for_zep_empty_and_whitespace() -> None:
    assert doc.chunk_for_zep("") == []
    assert doc.chunk_for_zep("   \n\t  ") == []


def test_chunk_for_zep_splits_oversized_paragraph() -> None:
    big = "word " * 3000  # >> DEFAULT_MAX_CHARS
    chunks = doc.chunk_for_zep(big, max_chars=100, overlap=10)
    assert len(chunks) > 1
    assert all(len(c) <= 100 for c in chunks)


def test_chunk_for_zep_packs_paragraphs() -> None:
    parts = ["para one text.", "para two more."]
    text = "\n\n".join(parts)
    chunks = doc.chunk_for_zep(text, max_chars=500)
    assert len(chunks) >= 1
    joined = "\n\n".join(chunks)
    assert "para one" in joined


def test_pdf_bytes_to_text_pypdf_extracts_text() -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    writer.write(buf)
    buf.seek(0)
    data = buf.read()
    # pypdf may get little text from blank page; use fitz to embed text
    fz = fitz.open(stream=data, filetype="pdf")
    page = fz.load_page(0)
    page.insert_text((72, 72), "HelloMedicalText")
    out_pdf = fz.tobytes()
    fz.close()
    text = doc.pdf_bytes_to_text_pypdf(out_pdf)
    assert "HelloMedicalText" in text


def test_data_note_dir_respects_patched_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(doc, "_REPO_ROOT", tmp_path)
    d = doc.data_note_dir("radiology_note")
    assert d == tmp_path / "data" / "radiology_note"


def test_ingest_txt_path_rejects_escape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doc, "_REPO_ROOT", tmp_path)
    rad = tmp_path / "data" / "radiology_note"
    rad.mkdir(parents=True)
    evil = tmp_path / "outside.txt"
    evil.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="must be under"):
        doc.ingest_txt_path_to_patient_graph("u1", evil, note_source="radiology_note")


def test_ingest_pdf_text_to_patient_graph_calls_graph_add(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ZEP_API_KEY", "k")
    from medtrace_agent.zep import memory

    memory.get_zep_client.cache_clear()
    mock_zep = MagicMock()
    ep = MagicMock()
    ep.uuid_ = "episode-uuid-1"
    mock_zep.graph.add.return_value = ep
    with patch("medtrace_agent.ingest.documents.get_zep_client", return_value=mock_zep):
        ids = doc.ingest_pdf_text_to_patient_graph(
            "user-1",
            "Some clinical text for ingestion.",
            filename="f.pdf",
            doc_id="fixed-id",
        )
    assert ids == ["episode-uuid-1"]
    assert mock_zep.graph.add.call_count >= 1
    first_kw = mock_zep.graph.add.call_args.kwargs
    assert first_kw["user_id"] == "user-1"
    assert first_kw["type"] == "text"
    assert first_kw["metadata"]["doc_id"] == "fixed-id"
