from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from functools import cached_property
from typing import Any

import httpx

from medtrace_agent.imaging.storage import study_preview_path


class MedGemmaService:
    """Connects report generation routes to MedGemma.

    Supported modes:
    - MEDGEMMA_ENDPOINT=http://... for a separate model server.
    - MEDGEMMA_MODEL_ID=google/medgemma-4b-it for local Hugging Face inference.
    - no env vars: deterministic mock output so the app still runs.
    """

    def __init__(self) -> None:
        self.endpoint = os.getenv("MEDGEMMA_ENDPOINT")
        self.model_id = os.getenv("MEDGEMMA_MODEL_ID")
        self.device = os.getenv("MEDGEMMA_DEVICE", "auto")
        self.nebius_api_key = os.getenv("NEBIUS_API_KEY")
        self.nebius_base_url = os.getenv("NEBIUS_BASE_URL", "https://api.tokenfactory.nebius.com/v1")
        self.nebius_model = os.getenv("NEBIUS_QWEN_VL_MODEL", "Qwen/Qwen2.5-VL-72B-Instruct")

    def generate_report(self, study_id: str, request: Any) -> dict[str, Any]:
        if self.nebius_api_key:
            return self._generate_report_nebius_qwen(study_id, request)

        if self.endpoint:
            return self._generate_report_http(study_id, request)

        if self.model_id:
            return self._generate_report_local(study_id, request)

        return self._mock_report(request)

    def status(self) -> dict[str, Any]:
        return {
            "provider": "qwen-vl" if self.nebius_api_key else "mock",
            "nebius_configured": bool(self.nebius_api_key),
            "model": self.nebius_model if self.nebius_api_key else None,
        }

    def _generate_report_nebius_qwen(self, study_id: str, request: Any) -> dict[str, Any]:
        preview_path = study_preview_path(study_id)
        image_content = []
        if preview_path.exists():
            image_content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{self._read_base64(preview_path)}",
                    },
                }
            )

        system_prompt = (
            "You are a radiology decision-support assistant. You are not a doctor and must not provide a final diagnosis. "
            "Analyze the provided radiology image for a clinician and return draft report content only. "
            "Be careful, concise, and mention uncertainty and need for clinician verification. "
            "Return only valid JSON with keys: summary, findings, impression, recommendation, confidence. "
            "confidence must be a number between 0 and 1."
        )
        user_text = (
            f"Create a draft radiology report for study {study_id}. "
            f"Modality: {request.modality}. Body part: {request.body_part}. "
            f"Segmentation ROI metadata: {json.dumps(request.segmentations)}. "
            "Use the image if present. Include observations, possible clinical significance, and limitations. "
            "Do not claim certainty and do not replace clinician review."
        )

        response = httpx.post(
            f"{self.nebius_base_url.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.nebius_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.nebius_model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": user_text},
                            *image_content,
                        ],
                    },
                ],
                "temperature": 0.2,
                "max_tokens": 900,
            },
            timeout=180,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        parsed = self._parse_report_json(content)

        return {
            "summary": parsed.get("summary", "Qwen VL draft assessment"),
            "findings": parsed.get("findings", content),
            "impression": parsed.get("impression", "Draft impression requires clinician verification."),
            "recommendation": parsed.get("recommendation", "Clinician review is required before sign-off."),
            "confidence": float(parsed.get("confidence", 0.5)),
            "source": "qwen-vl",
        }

    def _generate_report_http(self, study_id: str, request: Any) -> dict[str, Any]:
        response = httpx.post(
            f"{self.endpoint.rstrip('/')}/generate-report",
            json={
                "study_id": study_id,
                "modality": request.modality,
                "body_part": request.body_part,
                "segmentations": request.segmentations,
            },
            timeout=120,
        )
        response.raise_for_status()
        return response.json()

    def _generate_report_local(self, study_id: str, request: Any) -> dict[str, Any]:
        prompt = (
            "You are drafting radiology decision support for a doctor. "
            "Do not make autonomous clinical decisions. "
            "Return concise sections: summary, findings, impression, recommendation. "
            f"Study id: {study_id}. Modality: {request.modality}. Body part: {request.body_part}. "
            f"Segmentation metadata: {request.segmentations}."
        )

        result = self.pipeline(
            [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": prompt}],
                }
            ],
            max_new_tokens=512,
        )
        text = self._extract_text(result)

        return {
            "summary": "MedGemma draft assessment",
            "findings": text,
            "impression": "Review the generated findings and correlate clinically.",
            "recommendation": "Doctor verification is required before sign-off.",
            "confidence": 0.65,
            "source": "medgemma",
        }

    @cached_property
    def pipeline(self) -> Any:
        try:
            import torch
            from transformers import pipeline
        except ImportError as exc:
            raise RuntimeError(
                "Install torch and transformers, then set MEDGEMMA_MODEL_ID to run MedGemma locally."
            ) from exc

        torch_dtype = torch.bfloat16 if torch.cuda.is_available() else torch.float32
        return pipeline(
            "image-text-to-text",
            model=self.model_id,
            device_map=self.device,
            torch_dtype=torch_dtype,
        )

    def _extract_text(self, result: Any) -> str:
        if isinstance(result, list) and result:
            first = result[0]
            if isinstance(first, dict):
                generated = first.get("generated_text")
                if isinstance(generated, str):
                    return generated
                if isinstance(generated, list) and generated:
                    last = generated[-1]
                    if isinstance(last, dict):
                        content = last.get("content")
                        if isinstance(content, str):
                            return content
        return str(result)

    def _read_base64(self, path: Path) -> str:
        return base64.b64encode(path.read_bytes()).decode("ascii")

    def _parse_report_json(self, content: str) -> dict[str, Any]:
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            start = content.find("{")
            end = content.rfind("}")
            if start >= 0 and end > start:
                try:
                    return json.loads(content[start : end + 1])
                except json.JSONDecodeError:
                    pass
        return {
            "summary": "Qwen VL draft assessment",
            "findings": content,
            "impression": "Draft impression requires clinician verification.",
            "recommendation": "Clinician review is required before sign-off.",
            "confidence": 0.5,
        }

    def _mock_report(self, request: Any) -> dict[str, Any]:
        roi_count = len(request.segmentations)
        return {
            "summary": "Mock Qwen VL draft assessment",
            "findings": f"{request.modality} {request.body_part} study reviewed with {roi_count} segmentation ROI(s).",
            "impression": "Draft impression placeholder until Qwen VL is configured.",
            "recommendation": "Set NEBIUS_API_KEY in the repo .env and restart the API so Qwen VL can answer from the image.",
            "confidence": 0.78 if roi_count else 0.55,
            "source": "qwen-vl",
        }
