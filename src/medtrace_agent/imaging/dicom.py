"""DICOM reading and preview rendering.

Requires the ``imaging`` extra (``pip install -e ".[imaging]"``) for pydicom / numpy /
pillow; imports are deferred so the rest of the API starts without them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def is_dicom_file(path: Path) -> bool:
    """True when pydicom can parse the file header."""
    try:
        import pydicom

        pydicom.dcmread(path, stop_before_pixels=True)
        return True
    except Exception:
        return False


def render_dicom_preview(dicom_path: Path, study_dir: Path) -> dict[str, Any]:
    """Write ``preview.png`` into ``study_dir`` and return the study metadata.

    The PNG is a **thumbnail only** — an 8-bit projection of the middle frame. The viewer
    loads the original DICOM with Cornerstone3D so window/level operates on the full bit
    depth; this preview exists for the study list and as a no-WebGL fallback.

    Pixels are rescaled with ``RescaleSlope``/``RescaleIntercept`` and windowed with
    ``WindowCenter``/``WindowWidth`` when present, else stretched to the 1–99 percentile.
    """
    try:
        import numpy as np
        import pydicom
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError(
            "DICOM preview needs pydicom, numpy and pillow: pip install -e '.[imaging]'"
        ) from exc

    dataset = pydicom.dcmread(dicom_path)
    pixels = dataset.pixel_array.astype("float32")

    # Multi-frame studies are volumes. Thumbnail the middle frame — frame 0 of a CT/MR
    # stack is usually an end slice with little anatomy in it.
    frame_count = 1
    if pixels.ndim > 2:
        frame_count = int(pixels.shape[0])
        pixels = pixels[frame_count // 2]

    slope = float(getattr(dataset, "RescaleSlope", 1))
    intercept = float(getattr(dataset, "RescaleIntercept", 0))
    pixels = pixels * slope + intercept

    center = getattr(dataset, "WindowCenter", None)
    width = getattr(dataset, "WindowWidth", None)
    if isinstance(center, pydicom.multival.MultiValue):
        center = center[0]
    if isinstance(width, pydicom.multival.MultiValue):
        width = width[0]

    if center is not None and width is not None:
        center = float(center)
        width = float(width)
        low = center - width / 2
        high = center + width / 2
    else:
        low, high = np.percentile(pixels, [1, 99])

    pixels = np.clip((pixels - low) / max(high - low, 1), 0, 1)
    image = Image.fromarray((pixels * 255).astype("uint8"), mode="L")
    study_dir.mkdir(parents=True, exist_ok=True)
    image.save(study_dir / "preview.png")

    # NumberOfFrames is authoritative when present; fall back to the pixel array shape.
    frames = int(getattr(dataset, "NumberOfFrames", 0) or 0) or frame_count

    return {
        "patient_name": str(getattr(dataset, "PatientName", "Uploaded Study")).replace("^", " "),
        "patient_detail": str(getattr(dataset, "PatientID", "DICOM metadata")),
        "modality": str(getattr(dataset, "Modality", "DICOM")),
        "body_part": str(getattr(dataset, "BodyPartExamined", "Unspecified")).title(),
        "series": str(getattr(dataset, "SeriesDescription", "DICOM series")),
        "slices": frames,
    }
