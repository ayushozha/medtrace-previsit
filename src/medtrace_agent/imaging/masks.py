"""Persisting volumetric segmentation masks.

One segmentation = one directory under the study:

    data/studies/{study_id}/segmentations/{segmentation_id}/
        mask.bin            raw uint8 [D, H, W], C-order — fetched whole by the viewer and
                            copied into a Cornerstone3D labelmap volume
        meta.json           shape, spacing, prompt slice, voxel count, volume_ml
        slices/{i:04d}.png  RGBA overlay per mask-bearing slice, for the 2D stack viewer

The flat ``segmentations/{id}.png`` files next to this directory belong to the legacy
single-image path and are untouched.
"""

from __future__ import annotations

import json
from typing import Any

from medtrace_agent.imaging.storage import (
    segmentation_dir,
    segmentation_mask_url,
    segmentation_slice_overlay_url,
)
from medtrace_agent.imaging.volume import mask_volume_ml

#: Cyan tint shared with the legacy 2D overlay renderer, so masks look identical
#: whichever path produced them.
_OVERLAY_RGBA = (34, 211, 238, 165)


def save_mask_artifacts(
    study_id: str,
    segmentation_id: str,
    mask: Any,
    spacing: tuple[float, float, float],
    prompt_slice: int,
) -> dict[str, Any]:
    """Write mask.bin + meta.json + per-slice overlays; return the response fields.

    ``slice_overlay_urls`` is indexed by slice: ``None`` where the mask is empty, so the
    viewer shows nothing rather than a stale overlay while scrolling.
    """
    try:
        import numpy as np
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError(
            "Mask persistence needs numpy and pillow: pip install -e '.[imaging]'"
        ) from exc

    mask = np.ascontiguousarray(mask.astype("uint8"))
    if mask.ndim != 3:
        raise ValueError(f"Expected a [D, H, W] mask, got shape {mask.shape}")

    seg_dir = segmentation_dir(study_id, segmentation_id)
    slices_dir = seg_dir / "slices"
    slices_dir.mkdir(parents=True, exist_ok=True)

    mask.tofile(seg_dir / "mask.bin")

    depth = int(mask.shape[0])
    overlay_urls: list[str | None] = [None] * depth
    rgba = np.zeros((*mask.shape[1:], 4), dtype=np.uint8)
    for i in range(depth):
        on = mask[i].astype(bool)
        if not on.any():
            continue
        rgba[:] = 0
        rgba[on] = np.array(_OVERLAY_RGBA, dtype=np.uint8)
        Image.fromarray(rgba, mode="RGBA").save(slices_dir / f"{i:04d}.png")
        overlay_urls[i] = segmentation_slice_overlay_url(study_id, segmentation_id, i)

    volume_ml = mask_volume_ml(mask, spacing)
    voxel_count = int(mask.astype(bool).sum())
    dz, dy, dx = spacing

    meta = {
        "shape": [int(v) for v in mask.shape],
        "spacing_mm": [dz, dy, dx],
        "prompt_slice": int(prompt_slice),
        "voxel_count": voxel_count,
        "volume_ml": volume_ml,
    }
    (seg_dir / "meta.json").write_text(json.dumps(meta, indent=2))

    return {
        "volume_ml": volume_ml,
        "prompt_slice": int(prompt_slice),
        "mask_url": segmentation_mask_url(study_id, segmentation_id),
        "mask_shape": meta["shape"],
        "voxel_spacing_mm": meta["spacing_mm"],
        "slice_overlay_urls": overlay_urls,
        # Prompt-slice overlay keeps the legacy field meaningful for old clients.
        "overlay_url": overlay_urls[prompt_slice] if 0 <= prompt_slice < depth else None,
    }
