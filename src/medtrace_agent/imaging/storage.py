"""On-disk layout for imaging studies.

Single source of truth for where study files live. Everything sits under repo-root
``data/studies/``; FastAPI mounts only that imaging subtree at ``/data/studies``.

    data/studies/{study_id}/preview.png
    data/studies/{study_id}/segmentations/{segmentation_id}.png           (legacy 2D masks)
    data/studies/{study_id}/segmentations/{segmentation_id}/              (volumetric masks)
        mask.bin · meta.json · slices/{i:04d}.png
"""

from __future__ import annotations

from pathlib import Path

# .../src/medtrace_agent/imaging/storage.py -> parents[3] is the repo root.
_REPO_ROOT = Path(__file__).resolve().parents[3]

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


def data_dir() -> Path:
    """Repo-root ``data/``; only its ``studies`` child is served."""
    return _REPO_ROOT / "data"


def studies_dir() -> Path:
    """``data/studies``, created on first use."""
    d = data_dir() / "studies"
    d.mkdir(parents=True, exist_ok=True)
    return d


def study_dir(study_id: str) -> Path:
    return studies_dir() / study_id


def study_preview_path(study_id: str) -> Path:
    return study_dir(study_id) / "preview.png"


def study_overlay_url(study_id: str, segmentation_id: str) -> str:
    return f"/data/studies/{study_id}/segmentations/{segmentation_id}.png"


def segmentation_dir(study_id: str, segmentation_id: str) -> Path:
    """Directory holding one volumetric segmentation's artifacts."""
    return study_dir(study_id) / "segmentations" / segmentation_id


def segmentation_mask_url(study_id: str, segmentation_id: str) -> str:
    """Raw uint8 ``[D, H, W]`` mask bytes, loaded whole by the viewer as a labelmap."""
    return f"/data/studies/{study_id}/segmentations/{segmentation_id}/mask.bin"


def segmentation_slice_overlay_url(study_id: str, segmentation_id: str, slice_index: int) -> str:
    return f"/data/studies/{study_id}/segmentations/{segmentation_id}/slices/{slice_index:04d}.png"


def study_preview_url(study_id: str) -> str:
    return f"/data/studies/{study_id}/preview.png"


def study_dicom_url(study_id: str, filename: str) -> str:
    """URL of the original DICOM, which the viewer loads with Cornerstone3D.

    Rendering happens client-side on the full bit depth, so the browser needs the source
    file — not just the 8-bit preview PNG.
    """
    return f"/data/studies/{study_id}/{filename}"


def study_image_path(study_id: str) -> Path:
    """Preview PNG if rendered, else the first image file in the study directory."""
    preview = study_preview_path(study_id)
    if preview.is_file():
        return preview
    for path in sorted(study_dir(study_id).glob("*")):
        if path.is_file() and path.suffix.lower() in _IMAGE_SUFFIXES:
            return path
    return preview
