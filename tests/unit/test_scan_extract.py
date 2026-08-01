"""Unit tests for ``medtrace_agent.ingest.scan_extract``."""

from __future__ import annotations

import io
from unittest.mock import MagicMock, patch

import fitz
import pytest

from medtrace_agent.ingest.scan_extract import (
    LabRow,
    PageVLMExtract,
    _extract_json_object,
    pdf_to_page_images_png,
    serialize_pages_for_ingest,
    vl_extract_single_page,
)


def test_extract_json_object_raw_and_fenced() -> None:
    raw = '{"a": 1}'
    assert _extract_json_object(raw)["a"] == 1
    fenced = 'text\n```json\n{"b": 2}\n```'
    assert _extract_json_object(fenced)["b"] == 2


def test_serialize_pages_for_ingest_roundtrip() -> None:
    pages = [
        PageVLMExtract(
            page_number=1,
            page_visible_text="Line one",
            labs=[LabRow(name="Na", value="140")],
        )
    ]
    text = serialize_pages_for_ingest(pages)
    assert "Clinical document (VLM page extracts)" in text
    assert "Line one" in text
    assert "Na" in text


def test_pdf_to_page_images_png_renders_png() -> None:
    doc = fitz.open()
    doc.new_page()
    pdf_bytes = doc.tobytes()
    doc.close()
    images = pdf_to_page_images_png(pdf_bytes, dpi=72, max_pages=5)
    assert len(images) == 1
    assert images[0][:8] == b"\x89PNG\r\n\x1a\n"


def test_pdf_to_page_images_png_rejects_too_many_pages() -> None:
    doc = fitz.open()
    for _ in range(5):
        doc.new_page()
    pdf_bytes = doc.tobytes()
    doc.close()
    with pytest.raises(ValueError, match="max allowed"):
        pdf_to_page_images_png(pdf_bytes, max_pages=2)


def test_vl_extract_single_page_parses_llm_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FIREWORKS_API_KEY", "k")
    monkeypatch.setenv("FIREWORKS_VLM_API", "chat")
    payload = (
        '{"page_number":1,"document_type":null,'
        '"patient_identifiers_if_visible":[],"dates":[],"labs":[],"medications":[],'
        '"diagnoses_or_impressions":[],"imaging_or_figure_notes":[],"illegible_fields":[],'
        '"page_visible_text":"Hi"}'
    )
    mock_llm = MagicMock()
    mock_llm.invoke.return_value = MagicMock(content=payload)
    with patch("medtrace_agent.ingest.scan_extract._get_vlm_llm", return_value=mock_llm):
        out = vl_extract_single_page(b"x", page_index=1, total_pages=1, model="m")
    assert out.page_visible_text == "Hi"
