"""On-disk layout for imaging studies.

Single source of truth for where study files live. Everything sits under the repo-root
``data/`` directory alongside the other data folders (``data/local_mock``,
``data/radiology_note``, …) and is served by FastAPI at ``/data``.

    data/studies/{study_id}/preview.png
    data/studies/{study_id}/segmentations/{segmentation_id}.png
"""

from __future__ import annotations

from pathlib import Path

# .../src/medtrace_agent/imaging/storage.py -> parents[3] is the repo root.
_REPO_ROOT = Path(__file__).resolve().parents[3]

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


def data_dir() -> Path:
    """Repo-root ``data/`` — the directory mounted at ``/data``."""
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
