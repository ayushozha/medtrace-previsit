from __future__ import annotations

import importlib
import os
from io import BytesIO
from time import time_ns
from typing import Any

import httpx
import numpy as np
from PIL import Image

from medtrace_agent.imaging.storage import (
    study_dir,
    study_image_path,
    study_overlay_url,
)


class MedSAM2Service:
    """Connects segmentation routes to MedSAM2.

    Supported modes:
    - MEDSAM2_ENDPOINT=http://... for a separate MedSAM2 inference server.
    - MEDSAM2_ADAPTER_MODULE=your_module with a segment(study_id, prompt) function.
    - no env vars: deterministic mock output so the app still runs.

    MedSAM2 repositories are research-code style rather than one stable pip API,
    so the local path is intentionally adapter based.
    """

    def __init__(self) -> None:
        self.endpoint = os.getenv("MEDSAM2_ENDPOINT")
        self.adapter_module = os.getenv("MEDSAM2_ADAPTER_MODULE")

    def segment(self, study_id: str, prompt: Any) -> dict[str, Any]:
        payload = {
            "study_id": study_id,
            "prompt": {
                "x": prompt.x,
                "y": prompt.y,
                "width": prompt.width,
                "height": prompt.height,
            },
        }

        if self.endpoint:
            return self._segment_http(study_id=study_id, prompt=prompt, payload=payload)

        if self.adapter_module:
            return self._segment_adapter(study_id, prompt)

        return self._mock_segmentation(study_id, prompt)

    def _segment_http(self, study_id: str, prompt: Any, payload: dict[str, Any]) -> dict[str, Any]:
        """Call HTTP MedSAM endpoint.

        Supports two endpoint contracts:
        1) JSON contract used by this backend (study_id + normalized ROI prompt).
        2) Swin-LiteMedSAM contract (multipart file + x_min/y_min/x_max/y_max).

        No in-repo server implements (2) any more — the ``services/medsamlite`` demo that
        did was removed. Kept because it is a generic contract any external Swin-LiteMedSAM
        deployment can satisfy via ``MEDSAM2_ENDPOINT``.
        """
        endpoint = f"{self.endpoint.rstrip('/')}/segment"

        # First try the backend-native JSON contract.
        try:
            response = httpx.post(endpoint, json=payload, timeout=180)
            response.raise_for_status()
            parsed = response.json()
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            # Fallback to Swin-LiteMedSAM multipart contract.
            pass

        preview_path = study_image_path(study_id)
        if not preview_path.is_file():
            raise FileNotFoundError(f"Study image not found for MedSAM2 HTTP fallback: {preview_path}")

        with Image.open(preview_path) as image:
            width, height = image.size

        x_min = float(prompt.x) * width
        y_min = float(prompt.y) * height
        x_max = float(prompt.x + prompt.width) * width
        y_max = float(prompt.y + prompt.height) * height

        image_bytes = preview_path.read_bytes()
        files = {"file": (preview_path.name, BytesIO(image_bytes), "image/png")}
        data = {
            "x_min": str(x_min),
            "y_min": str(y_min),
            "x_max": str(x_max),
            "y_max": str(y_max),
        }
        response = httpx.post(endpoint, files=files, data=data, timeout=300)
        response.raise_for_status()
        seg_id = f"seg-{study_id}-{int(x_min)}-{int(y_min)}-{time_ns()}"
        overlay_url, area_ratio = self._save_mask_overlay(
            study_id=study_id,
            segmentation_id=seg_id,
            overlay_bytes=response.content,
        )

        # Swin-Lite endpoint returns image bytes, not structured JSON.
        # Convert to the backend's expected segmentation response shape.
        return {
            "id": seg_id,
            "label": "Swin-LiteMedSAM ROI",
            "confidence": round(min(0.99, 0.55 + area_ratio * 1.7), 2),
            "volume_ml": round(max(0.1, area_ratio * 1200), 1),
            "source": "medsam2",
            "overlay_url": overlay_url,
            "box": {
                "x": prompt.x,
                "y": prompt.y,
                "width": prompt.width,
                "height": prompt.height,
            },
        }

    def _save_mask_overlay(self, study_id: str, segmentation_id: str, overlay_bytes: bytes) -> tuple[str, float]:
        """Persist a transparent mask overlay and return URL + mask area ratio."""
        overlays_dir = study_dir(study_id) / "segmentations"
        overlays_dir.mkdir(parents=True, exist_ok=True)

        overlay = np.array(Image.open(BytesIO(overlay_bytes)).convert("RGB"))
        # Detect green-tinted mask regions from Swin output.
        mask = (overlay[:, :, 1] > overlay[:, :, 0] + 24) & (overlay[:, :, 1] > overlay[:, :, 2] + 24)
        area_ratio = float(mask.mean()) if mask.size else 0.0

        rgba = np.zeros((overlay.shape[0], overlay.shape[1], 4), dtype=np.uint8)
        rgba[mask] = np.array([34, 211, 238, 165], dtype=np.uint8)

        output_path = overlays_dir / f"{segmentation_id}.png"
        Image.fromarray(rgba, mode="RGBA").save(output_path)
        return study_overlay_url(study_id, segmentation_id), area_ratio

    def _segment_adapter(self, study_id: str, prompt: Any) -> dict[str, Any]:
        module = importlib.import_module(self.adapter_module or "")
        segment = getattr(module, "segment")
        result = segment(study_id=study_id, prompt=prompt)
        if not isinstance(result, dict):
            raise TypeError("MEDSAM2_ADAPTER_MODULE.segment must return a dict")
        return result

    def _mock_segmentation(self, study_id: str, prompt: Any) -> dict[str, Any]:
        """Echo the prompt back with fixed numbers — no model runs.

        ``source`` must stay ``mock`` so the UI cannot present a hand-drawn box as a model
        output. Set MEDSAM2_ENDPOINT or MEDSAM2_ADAPTER_MODULE for real segmentation.
        """
        return {
            "id": f"seg-{study_id}",
            "label": "Mock ROI — no segmentation model configured",
            "confidence": 0.0,
            "volume_ml": 0.0,
            "source": "mock",
            "box": {
                "x": prompt.x,
                "y": prompt.y,
                "width": prompt.width,
                "height": prompt.height,
            },
        }
