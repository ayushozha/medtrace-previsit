"""PDF → page PNGs (PyMuPDF) → VLM structured JSON + transcript → plain text for Zep ingest."""

from __future__ import annotations

import base64
import json
import os
import re
from typing import Any, Callable

import fitz  # PyMuPDF
import httpx
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from medtrace_agent.fireworks_config import (
    fireworks_api_key,
    fireworks_base_url,
    fireworks_chat_client,
    fireworks_reasoning_effort,
    fireworks_vlm_api_mode,
    fireworks_vlm_model,
)

VLM_SYSTEM_PROMPT = """You are a clinical document understanding assistant. You receive a single page image from a medical PDF.

Return ONE JSON object only (no markdown fences, no commentary). Rules:
- Extract ONLY what is visibly supported by the image. Do NOT invent patient names, numbers, diagnoses, or lab values.
- Use null or empty arrays for unknown fields. For unreadable fragments use "[illegible]" inside page_visible_text only.
- page_visible_text must contain a faithful transcription of all legible printed and handwritten text on the page (tables as plain text rows where possible).
- structured fields summarize the same evidence; do not contradict page_visible_text.

JSON shape (all keys required; use null or [] where appropriate):
{
  "page_number": <int or null>,
  "document_type": <string or null>,
  "patient_identifiers_if_visible": [<strings>],
  "dates": [<strings>],
  "labs": [{"name": null, "value": null, "unit": null, "ref_range": null, "flag": null}],
  "medications": [<strings>],
  "diagnoses_or_impressions": [<strings>],
  "imaging_or_figure_notes": [<brief factual notes about visible figures, or empty>],
  "illegible_fields": [<short descriptions of unreadable regions>],
  "page_visible_text": "<full transcript string>"
}
"""


class LabRow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str | None = None
    value: str | None = None
    unit: str | None = None
    ref_range: str | None = None
    flag: str | None = None


class PageVLMExtract(BaseModel):
    model_config = ConfigDict(extra="ignore")

    page_number: int | None = None
    document_type: str | None = None
    patient_identifiers_if_visible: list[str] = Field(default_factory=list)
    dates: list[str] = Field(default_factory=list)
    labs: list[LabRow] = Field(default_factory=list)
    medications: list[str] = Field(default_factory=list)
    diagnoses_or_impressions: list[str] = Field(default_factory=list)
    imaging_or_figure_notes: list[str] = Field(default_factory=list)
    illegible_fields: list[str] = Field(default_factory=list)
    page_visible_text: str | None = None


def pdf_to_page_images_png(
    data: bytes,
    *,
    dpi: int = 150,
    max_pages: int = 25,
) -> list[bytes]:
    """Render each PDF page to PNG bytes using PyMuPDF."""
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        n = doc.page_count
        if n > max_pages:
            raise ValueError(
                f"PDF has {n} pages; max allowed is {max_pages} (set PDF_VL_MAX_PAGES or pass max_pages)."
            )
        zoom = dpi / 72.0
        mat = fitz.Matrix(zoom, zoom)
        images: list[bytes] = []
        for i in range(n):
            page = doc.load_page(i)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            images.append(pix.tobytes("png"))
        return images
    finally:
        doc.close()


def _extract_json_object(raw: str) -> dict[str, Any]:
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()
    return json.loads(text)


def _get_vlm_llm(model: str) -> ChatOpenAI:
    return fireworks_chat_client(model, temperature=0.1, max_tokens=8192)


def _fireworks_completions_vlm_prompt(user_block: str) -> str:
    """Fireworks completions VL format: one ``<image>`` token per image in ``images``."""
    return (
        f"SYSTEM: {VLM_SYSTEM_PROMPT}\n\n"
        f"USER:<image>\n{user_block.strip()}\n\n"
        "ASSISTANT:"
    )


def _fireworks_completions_vlm_invoke(model: str, png_bytes: bytes, user_block: str) -> str:
    """POST ``/v1/completions`` with ``prompt`` + ``images`` (see Fireworks vision docs)."""
    b64 = base64.standard_b64encode(png_bytes).decode("ascii")
    data_uri = f"data:image/png;base64,{b64}"
    base = fireworks_base_url().rstrip("/")
    url = f"{base}/completions"
    payload: dict[str, Any] = {
        "model": model,
        "prompt": _fireworks_completions_vlm_prompt(user_block),
        "images": [data_uri],
        "max_tokens": 8192,
        "top_p": 1,
        "top_k": 40,
        "presence_penalty": 0,
        "frequency_penalty": 0,
        "temperature": 0.1,
        "reasoning_effort": fireworks_reasoning_effort(),
    }
    headers = {
        "Authorization": f"Bearer {fireworks_api_key()}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    timeout = float(os.environ.get("FIREWORKS_VLM_COMPLETIONS_TIMEOUT", "120"))
    with httpx.Client(timeout=timeout) as client:
        r = client.post(url, headers=headers, json=payload)
    try:
        data = r.json()
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Fireworks completions: non-JSON response ({r.status_code}): {r.text[:500]}") from exc
    if r.status_code >= 400:
        raise RuntimeError(f"Fireworks completions HTTP {r.status_code}: {data}")
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError(f"Fireworks completions: empty choices: {data}")
    text = choices[0].get("text")
    if text is None:
        raise RuntimeError(f"Fireworks completions: missing choices[0].text: {data}")
    return text


def vl_extract_single_page(
    png_bytes: bytes,
    *,
    page_index: int,
    total_pages: int,
    model: str,
) -> PageVLMExtract:
    """One VLM call per page; validates JSON with Pydantic, one repair retry."""
    user_text = (
        f"This is page {page_index} of {total_pages}. "
        "Analyze the image and output the single JSON object as specified in the system message."
    )

    def parse_response(content: str) -> PageVLMExtract:
        obj = _extract_json_object(content)
        return PageVLMExtract.model_validate(obj)

    mode = fireworks_vlm_api_mode()

    if mode == "completions":
        text_out = _fireworks_completions_vlm_invoke(model, png_bytes, user_text)
        try:
            return parse_response(text_out)
        except (json.JSONDecodeError, ValidationError) as first_err:
            repair_block = (
                f"{user_text}\n\n"
                "Your previous reply was not valid JSON matching the schema. "
                f"Error: {first_err}\n\n"
                "Reply with ONLY one raw JSON object (no markdown), matching the schema from the system message."
            )
            text2 = _fireworks_completions_vlm_invoke(model, png_bytes, repair_block)
            return parse_response(text2)

    llm = _get_vlm_llm(model)
    b64 = base64.standard_b64encode(png_bytes).decode("ascii")
    data_uri = f"data:image/png;base64,{b64}"
    messages = [
        SystemMessage(content=VLM_SYSTEM_PROMPT),
        HumanMessage(
            content=[
                {"type": "text", "text": user_text},
                {"type": "image_url", "image_url": {"url": data_uri}},
            ]
        ),
    ]

    resp = llm.invoke(messages)
    raw_content = resp.content
    text_out = raw_content if isinstance(raw_content, str) else str(raw_content)

    try:
        return parse_response(text_out)
    except (json.JSONDecodeError, ValidationError) as first_err:
        repair = HumanMessage(
            content=(
                "Your previous reply was not valid JSON matching the schema. "
                f"Error: {first_err}\n\n"
                "Reply with ONLY one raw JSON object (no markdown), matching the schema from the system message."
            )
        )
        resp2 = llm.invoke([SystemMessage(content=VLM_SYSTEM_PROMPT), messages[1], repair])
        raw2 = resp2.content
        text2 = raw2 if isinstance(raw2, str) else str(raw2)
        return parse_response(text2)


def serialize_pages_for_ingest(pages: list[PageVLMExtract]) -> str:
    """Turn validated page extracts into one plain-text document for chunk_for_zep."""
    blocks: list[str] = ["# Clinical document (VLM page extracts)\n"]
    for idx, p in enumerate(pages, start=1):
        blocks.append(f"\n## Page {idx}\n")
        blocks.append("\n### Structured clinical extract\n")
        data = p.model_dump(exclude_none=True)
        transcript = data.pop("page_visible_text", None)
        blocks.append(json.dumps(data, indent=2, ensure_ascii=False))
        blocks.append("\n### Full visible text (transcript)\n")
        blocks.append((transcript or "").strip() or "[No transcript extracted]")
    return "\n".join(blocks).strip()


def pdf_bytes_via_vlm(
    data: bytes,
    *,
    dpi: int | None = None,
    max_pages: int | None = None,
    progress_cb: Callable[[int, int], None] | None = None,
) -> str:
    """
    Full pipeline: PDF bytes → PNG per page → VLM extract each page → single ingest string.
    """
    dpi_eff = dpi if dpi is not None else int(os.environ.get("PDF_VL_DPI", "150"))
    max_eff = max_pages if max_pages is not None else int(os.environ.get("PDF_VL_MAX_PAGES", "25"))

    model = fireworks_vlm_model()

    images = pdf_to_page_images_png(data, dpi=dpi_eff, max_pages=max_eff)
    total = len(images)
    pages_out: list[PageVLMExtract] = []
    for i, png in enumerate(images):
        if progress_cb:
            progress_cb(i + 1, total)
        pages_out.append(
            vl_extract_single_page(
                png,
                page_index=i + 1,
                total_pages=total,
                model=model,
            )
        )
    return serialize_pages_for_ingest(pages_out)
