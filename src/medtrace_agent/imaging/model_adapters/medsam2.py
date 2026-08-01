from __future__ import annotations

import importlib
import os
import tempfile
from io import BytesIO
from pathlib import Path
from time import time_ns
from typing import Any

import httpx
import numpy as np
from PIL import Image

from medtrace_agent.imaging.masks import save_mask_artifacts
from medtrace_agent.imaging.storage import (
    study_dir,
    study_image_path,
    study_overlay_url,
)
from medtrace_agent.imaging.volume import (
    default_window,
    load_nifti_mask,
    series_to_nifti,
    study_series_paths,
)


class MedSAM2Service:
    """Connects segmentation routes to MedSAM2.

    Supported modes:
    - MEDSAM2_ENDPOINT=http://... for a separate MedSAM2 inference server
      (plus MEDSAM2_API_KEY when the server requires a bearer token).
    - MEDSAM2_ADAPTER_MODULE=your_module with a segment(study_id, prompt) function,
      and optionally segment_volume(study_id, volume, spacing, box_px, slice_index)
      for volumetric studies.
    - no env vars: deterministic mock output so the app still runs.

    A multi-slice series goes through the volumetric path: the whole stack is segmented
    from one prompted slice, and the mask propagates in 3D. Single-image studies keep the
    original 2D behaviour.

    MedSAM2 repositories are research-code style rather than one stable pip API,
    so the local path is intentionally adapter based.
    """

    def __init__(self) -> None:
        self.endpoint = os.getenv("MEDSAM2_ENDPOINT")
        self.adapter_module = os.getenv("MEDSAM2_ADAPTER_MODULE")
        self.api_key = os.getenv("MEDSAM2_API_KEY")
        self.timeout_s = float(os.getenv("MEDSAM2_TIMEOUT_S", "300"))

    def segment(self, study_id: str, prompt: Any) -> dict[str, Any]:
        series_paths = study_series_paths(study_id)
        if len(series_paths) >= 2:
            return self._segment_volumetric(study_id, prompt, series_paths)

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

    # ------------------------------------------------------------------ volumetric path

    def _segment_volumetric(
        self, study_id: str, prompt: Any, series_paths: list[Path]
    ) -> dict[str, Any]:
        with tempfile.TemporaryDirectory() as tmp:
            nii_path = Path(tmp) / "volume.nii.gz"
            volume, spacing = series_to_nifti(series_paths, nii_path)
            depth, height, width = volume.shape

            slice_index = getattr(prompt, "slice_index", None)
            prompt_slice = int(slice_index) if slice_index is not None else depth // 2
            prompt_slice = max(0, min(depth - 1, prompt_slice))

            x_min = max(0, min(width - 1, round(float(prompt.x) * width)))
            y_min = max(0, min(height - 1, round(float(prompt.y) * height)))
            x_max = max(x_min + 1, min(width, round(float(prompt.x + prompt.width) * width)))
            y_max = max(y_min + 1, min(height, round(float(prompt.y + prompt.height) * height)))
            box_px = (x_min, y_min, x_max, y_max)

            # Intensity window MedSAM2 normalises with: the caller's viewer window/level,
            # else the series' own DICOM window. Full-range normalisation flattens soft
            # tissue and makes the model over-segment.
            wl = getattr(prompt, "window_lower", None)
            wu = getattr(prompt, "window_upper", None)
            window = (float(wl), float(wu)) if wl is not None and wu is not None else default_window(series_paths)

            if self.endpoint:
                try:
                    mask = self._volume_mask_http(
                        nii_path, volume.shape, box_px, prompt_slice, window
                    )
                except Exception:
                    # Server unreachable or speaking a different contract: degrade to the
                    # legacy single-image call rather than failing the request.
                    return self._segment_http(
                        study_id=study_id,
                        prompt=prompt,
                        payload={"study_id": study_id, "prompt": prompt.model_dump()},
                    )
                return self._volumetric_result(
                    study_id, prompt, mask, spacing, prompt_slice, source="medsam2"
                )

            if self.adapter_module:
                module = importlib.import_module(self.adapter_module)
                segment_volume = getattr(module, "segment_volume", None)
                if not callable(segment_volume):
                    return self._segment_adapter(study_id, prompt)
                mask = np.asarray(
                    segment_volume(
                        study_id=study_id,
                        volume=volume,
                        spacing=spacing,
                        box_px=box_px,
                        slice_index=prompt_slice,
                    )
                )
                if mask.shape != volume.shape:
                    raise ValueError(
                        f"segment_volume returned {mask.shape}, expected {volume.shape}"
                    )
                return self._volumetric_result(
                    study_id, prompt, mask, spacing, prompt_slice, source="medsam2"
                )

            mask = self._mock_volume_mask(volume.shape, box_px, prompt_slice, spacing)
            return self._volumetric_result(
                study_id, prompt, mask, spacing, prompt_slice, source="mock"
            )

    def _volume_mask_http(
        self,
        nii_path: Path,
        expected_shape: tuple[int, int, int],
        box_px: tuple[int, int, int, int],
        prompt_slice: int,
        window: tuple[float, float] | None = None,
    ) -> Any:
        """MedSAM2 3D inference gateway contract (medical-ai-hub / medsam2.github.io).

        ``POST {endpoint}/v1/medsam2/segment/3d`` (behind an ``Authorization: Bearer``
        gateway) with a NIfTI volume, ``key_slice_index``, a pixel ``bbox`` and
        ``output_format``. The gateway answers with an ``.npz`` whose ``mask`` array is the
        volumetric mask in ``[D, H, W]`` order (a ``.nii.gz`` body is also accepted).

        ``MEDSAM2_ENDPOINT`` must point at the gateway base, e.g.
        ``http://<host>:8080`` — NOT the port 7860 Gradio test UI, which is not an API.
        """
        endpoint = self.endpoint.rstrip("/")
        if not endpoint.endswith("/segment/3d"):
            endpoint = f"{endpoint}/v1/medsam2/segment/3d"

        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        x_min, y_min, x_max, y_max = box_px
        data = {
            "key_slice_index": str(prompt_slice),
            "bbox": f"{x_min},{y_min},{x_max},{y_max}",
            "output_format": "npz",
        }
        if window is not None:
            data["lower_bound"], data["upper_bound"] = str(window[0]), str(window[1])
        with nii_path.open("rb") as nii:
            response = httpx.post(
                endpoint,
                headers=headers,
                files={"file": (nii_path.name, nii, "application/gzip")},
                data=data,
                timeout=self.timeout_s,
            )
        response.raise_for_status()
        return self._parse_mask_response(response.content, nii_path.parent, expected_shape)

    @staticmethod
    def _parse_mask_response(
        content: bytes, work_dir: Path, expected_shape: tuple[int, int, int]
    ) -> Any:
        """Decode the gateway's mask body — ``.npz`` (``mask`` key) or ``.nii.gz``.

        Distinguished by magic bytes: ``PK`` is a NumPy ``.npz`` (zip); ``\\x1f\\x8b`` is a
        gzipped NIfTI. The mask is normalised to binary ``uint8 [D, H, W]`` matching the
        study volume, transposing if the server answered in ``[W, H, D]`` order.
        """
        if content[:2] == b"PK":
            data = np.load(BytesIO(content), allow_pickle=True)
            key = "mask" if "mask" in data.files else data.files[0]
            mask = (np.asarray(data[key]) > 0).astype("uint8")
            if mask.shape == tuple(reversed(expected_shape)):
                mask = mask.transpose(2, 1, 0)
            if mask.shape != expected_shape:
                raise ValueError(
                    f"MedSAM2 mask grid {mask.shape} does not match the study volume "
                    f"{expected_shape}"
                )
            return np.ascontiguousarray(mask)

        mask_path = work_dir / "mask.nii.gz"
        mask_path.write_bytes(content)
        return load_nifti_mask(mask_path, expected_shape=expected_shape)

    def _mock_volume_mask(
        self,
        shape: tuple[int, int, int],
        box_px: tuple[int, int, int, int],
        prompt_slice: int,
        spacing: tuple[float, float, float],
    ) -> Any:
        """Deterministic ellipsoid centred on the prompt — no model runs.

        In-plane radii come from the drawn box; the z radius is scaled by voxel
        anisotropy so the shape is spherical in millimetres, which makes the MPR
        labelmap a legible geometry check even in mock mode.
        """
        depth, height, width = shape
        dz, dy, dx = spacing
        x_min, y_min, x_max, y_max = box_px

        cx, cy = (x_min + x_max) / 2, (y_min + y_max) / 2
        rx = max((x_max - x_min) / 2, 1.0)
        ry = max((y_max - y_min) / 2, 1.0)
        rz = max(min(rx * dx, ry * dy) / dz, 1.0)

        zz, yy, xx = np.ogrid[0:depth, 0:height, 0:width]
        ellipsoid = (
            ((xx - cx) / rx) ** 2
            + ((yy - cy) / ry) ** 2
            + ((zz - prompt_slice) / rz) ** 2
        )
        return (ellipsoid <= 1.0).astype("uint8")

    def _volumetric_result(
        self,
        study_id: str,
        prompt: Any,
        mask: Any,
        spacing: tuple[float, float, float],
        prompt_slice: int,
        *,
        source: str,
    ) -> dict[str, Any]:
        mask = np.ascontiguousarray(mask.astype("uint8"))
        if source == "mock":
            # Deterministic id: repeated identical prompts overwrite the same artifacts.
            x_min, y_min = round(prompt.x * 1000), round(prompt.y * 1000)
            seg_id = f"seg-{study_id}-mock-{prompt_slice}-{x_min}-{y_min}"
            label = "Mock ellipsoid — no segmentation model configured"
            confidence = 0.0
        else:
            seg_id = f"seg-{study_id}-{prompt_slice}-{time_ns()}"
            label = "MedSAM2 3D ROI"
            # The server returns only the mask; score by prompt-slice coverage, the same
            # heuristic the legacy 2D path used.
            area_ratio = float(mask[prompt_slice].mean())
            confidence = round(min(0.99, 0.55 + area_ratio * 1.7), 2)

        artifacts = save_mask_artifacts(study_id, seg_id, mask, spacing, prompt_slice)
        return {
            "id": seg_id,
            "label": label,
            "confidence": confidence,
            "source": source,
            "box": {
                "x": prompt.x,
                "y": prompt.y,
                "width": prompt.width,
                "height": prompt.height,
                "slice_index": getattr(prompt, "slice_index", None),
            },
            **artifacts,
        }

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
