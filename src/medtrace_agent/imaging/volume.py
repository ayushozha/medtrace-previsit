"""Series → 3D volume assembly and NIfTI interchange.

Turns an ordered DICOM series into a float32 ``[depth, rows, cols]`` array with real voxel
spacing, and converts it to/from NIfTI for the MedSAM2 3D inference server. Requires the
``imaging`` extra (pydicom / numpy / nibabel); imports are deferred so the rest of the API
starts without them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from medtrace_agent.imaging.series import sort_series


def study_series_paths(study_id: str) -> list[Path]:
    """DICOM files of a study, in anatomical order.

    Uploads keep their original leaf names (not necessarily ``*.dcm``), so membership is
    decided by parsing, not extension. ``preview.png`` and ``segmentations/`` live in the
    same directory and are skipped as non-DICOM.
    """
    from medtrace_agent.imaging.dicom import is_dicom_file
    from medtrace_agent.imaging.storage import study_dir

    root = study_dir(study_id)
    if not root.is_dir():
        return []
    candidates = [p for p in sorted(root.iterdir()) if p.is_file() and is_dicom_file(p)]
    return [s.path for s in sort_series(candidates)]


def _slice_spacing_mm(datasets: list[Any], positions: list[float | None]) -> float:
    """Distance between slice centres, in mm.

    The measured step between projected ``ImagePositionPatient`` values is authoritative —
    ``SliceThickness`` describes the acquisition, not the reconstruction increment, and gaps
    or overlaps between slices are common. Median, not mean, so one dropped slice cannot
    skew the whole volume.
    """
    import numpy as np

    known = [p for p in positions if p is not None]
    if len(known) >= 2:
        deltas = np.abs(np.diff(np.asarray(known, dtype="float64")))
        deltas = deltas[deltas > 1e-6]
        if deltas.size:
            return float(np.median(deltas))
    first = datasets[0]
    for tag in ("SpacingBetweenSlices", "SliceThickness"):
        value = getattr(first, tag, None)
        if value is not None and float(value) > 0:
            return float(value)
    return 1.0


def load_series_volume(paths: list[Path]) -> tuple[Any, tuple[float, float, float]]:
    """Stack a DICOM series into ``(volume [D, H, W] float32, (dz, dy, dx) mm)``.

    Pixels are rescaled with ``RescaleSlope``/``RescaleIntercept`` so the volume holds real
    modality units (HU for CT) — the segmentation server does its own normalisation.
    """
    try:
        import numpy as np
        import pydicom
    except ImportError as exc:
        raise RuntimeError(
            "Volume assembly needs pydicom and numpy: pip install -e '.[imaging]'"
        ) from exc

    ordered = sort_series(paths)
    if not ordered:
        raise ValueError("No readable DICOM slices to assemble into a volume.")

    datasets = [pydicom.dcmread(s.path) for s in ordered]
    frames: list[Any] = []
    for ds in datasets:
        pixels = ds.pixel_array.astype("float32")
        slope = float(getattr(ds, "RescaleSlope", 1))
        intercept = float(getattr(ds, "RescaleIntercept", 0))
        frames.append(pixels * slope + intercept)

    shapes = {f.shape for f in frames}
    if len(shapes) != 1:
        raise ValueError(f"Series slices disagree on matrix size: {sorted(shapes)}")

    volume = np.stack(frames, axis=0)

    # PixelSpacing is [row spacing, column spacing] = [dy, dx].
    pixel_spacing = getattr(datasets[0], "PixelSpacing", None)
    if pixel_spacing is not None and len(pixel_spacing) == 2:
        dy, dx = float(pixel_spacing[0]), float(pixel_spacing[1])
    else:
        dy = dx = 1.0
    dz = _slice_spacing_mm(datasets, [s.position for s in ordered])
    return volume, (dz, dy, dx)


def _series_affine(paths: list[Path], spacing: tuple[float, float, float]) -> Any:
    """NIfTI affine mapping voxel ``(col, row, slice)`` indices to RAS mm.

    DICOM patient coordinates are LPS; NIfTI is RAS, so the x and y components flip sign.
    The slice direction comes from the measured step between the first and last
    ``ImagePositionPatient`` when present, else the row×col normal scaled by dz — either
    way the affine encodes the same geometry the viewer reslices on.
    """
    import numpy as np
    import pydicom

    dz, dy, dx = spacing
    ordered = sort_series(paths)
    first = pydicom.dcmread(ordered[0].path, stop_before_pixels=True)

    iop = getattr(first, "ImageOrientationPatient", None)
    if iop is not None and len(iop) == 6:
        row_cosine = np.asarray([float(v) for v in iop[:3]])  # direction of increasing column
        col_cosine = np.asarray([float(v) for v in iop[3:]])  # direction of increasing row
    else:
        row_cosine = np.asarray([1.0, 0.0, 0.0])
        col_cosine = np.asarray([0.0, 1.0, 0.0])

    ipp_first = getattr(first, "ImagePositionPatient", None)
    origin = (
        np.asarray([float(v) for v in ipp_first])
        if ipp_first is not None and len(ipp_first) == 3
        else np.zeros(3)
    )

    slice_step = np.cross(row_cosine, col_cosine) * dz
    if len(ordered) > 1 and ipp_first is not None:
        last = pydicom.dcmread(ordered[-1].path, stop_before_pixels=True)
        ipp_last = getattr(last, "ImagePositionPatient", None)
        if ipp_last is not None and len(ipp_last) == 3:
            step = (np.asarray([float(v) for v in ipp_last]) - origin) / (len(ordered) - 1)
            if np.linalg.norm(step) > 1e-6:
                slice_step = step

    lps_to_ras = np.diag([-1.0, -1.0, 1.0])
    affine = np.eye(4)
    affine[:3, 0] = lps_to_ras @ (row_cosine * dx)
    affine[:3, 1] = lps_to_ras @ (col_cosine * dy)
    affine[:3, 2] = lps_to_ras @ slice_step
    affine[:3, 3] = lps_to_ras @ origin
    return affine


def series_to_nifti(paths: list[Path], out_path: Path) -> tuple[Any, tuple[float, float, float]]:
    """Write the series as ``out_path`` (.nii.gz) and return ``(volume, spacing)``.

    The NIfTI data array is stored ``(cols, rows, slices)`` — the conventional
    ``vol[:, :, k]``-is-an-axial-slice layout that nibabel consumers expect — while the
    returned volume stays in this package's ``[D, H, W]`` order.
    """
    try:
        import nibabel as nib
        import numpy as np
    except ImportError as exc:
        raise RuntimeError(
            "NIfTI export needs nibabel: pip install -e '.[imaging]'"
        ) from exc

    volume, spacing = load_series_volume(paths)
    affine = _series_affine(paths, spacing)
    data = np.ascontiguousarray(volume.transpose(2, 1, 0)).astype("float32")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    nib.save(nib.Nifti1Image(data, affine), str(out_path))
    return volume, spacing


def load_nifti_mask(path: Path, expected_shape: tuple[int, int, int]) -> Any:
    """Read a NIfTI mask back into ``uint8 [D, H, W]`` matching our slice order.

    The server answers on the same voxel grid it was sent, i.e. ``(cols, rows, slices)``;
    transposing restores ``[D, H, W]``. Accepts a mask already in ``[D, H, W]`` too, in
    case a different server implementation echoes numpy order.
    """
    try:
        import nibabel as nib
        import numpy as np
    except ImportError as exc:
        raise RuntimeError(
            "NIfTI import needs nibabel: pip install -e '.[imaging]'"
        ) from exc

    data = np.asanyarray(nib.load(str(path)).dataobj)
    if data.ndim == 4:  # some tools append a singleton time axis
        data = data[..., 0]
    if data.ndim != 3:
        raise ValueError(f"Expected a 3D mask, got shape {data.shape}")

    mask = (data > 0.5).astype("uint8")
    if mask.shape == tuple(reversed(expected_shape)):
        mask = mask.transpose(2, 1, 0)
    if mask.shape != expected_shape:
        raise ValueError(
            f"Mask grid {mask.shape} does not match the study volume {expected_shape}"
        )
    return np.ascontiguousarray(mask)


def default_window(paths: list[Path]) -> tuple[float, float] | None:
    """The series' own display window as ``(lower, upper)`` HU, from the first slice.

    MedSAM2 expects images windowed to the anatomy of interest and normalised to 0–255;
    the DICOM ``WindowCenter`` / ``WindowWidth`` set by the scanner is the sensible default
    when the caller does not supply one. Returns ``None`` if the tags are absent.
    """
    try:
        import pydicom
    except ImportError:
        return None

    ordered = sort_series(paths)
    if not ordered:
        return None
    ds = pydicom.dcmread(ordered[0].path, stop_before_pixels=True)
    center = getattr(ds, "WindowCenter", None)
    width = getattr(ds, "WindowWidth", None)
    if center is None or width is None:
        return None
    if isinstance(center, pydicom.multival.MultiValue):
        center = center[0]
    if isinstance(width, pydicom.multival.MultiValue):
        width = width[0]
    center, width = float(center), float(width)
    if width <= 0:
        return None
    return center - width / 2, center + width / 2


def mask_volume_ml(mask: Any, spacing: tuple[float, float, float]) -> float:
    """Real segmented volume in millilitres: voxel count × voxel size (mm³ → mL)."""
    dz, dy, dx = spacing
    voxels = int(mask.astype(bool).sum())
    return round(voxels * dz * dy * dx / 1000.0, 2)
