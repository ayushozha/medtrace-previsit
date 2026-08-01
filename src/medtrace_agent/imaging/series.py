"""Ordering a DICOM series into a volume.

Files in a study arrive in whatever order the filesystem or the browser hands them over —
that order has nothing to do with anatomy. Slices must be sorted by their physical position
before they can be stacked into a volume, or the reconstruction is scrambled.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SeriesSlice:
    """One ordered slice of a series."""

    path: Path
    #: Distance along the slice normal, in mm. The sort key when geometry is present.
    position: float | None
    instance_number: int | None


def _read_header(path: Path) -> Any | None:
    try:
        import pydicom

        return pydicom.dcmread(path, stop_before_pixels=True)
    except Exception:
        return None


def _slice_position(dataset: Any) -> float | None:
    """Project ``ImagePositionPatient`` onto the slice normal.

    Sorting on the raw z coordinate breaks for oblique acquisitions, where slices step along
    a tilted axis. The normal is the cross product of the row and column direction cosines,
    so this is correct for any orientation.
    """
    ipp = getattr(dataset, "ImagePositionPatient", None)
    iop = getattr(dataset, "ImageOrientationPatient", None)
    if ipp is None or len(ipp) != 3:
        return None
    if iop is None or len(iop) != 6:
        return float(ipp[2])  # no orientation: fall back to raw z

    row = [float(v) for v in iop[:3]]
    col = [float(v) for v in iop[3:]]
    normal = (
        row[1] * col[2] - row[2] * col[1],
        row[2] * col[0] - row[0] * col[2],
        row[0] * col[1] - row[1] * col[0],
    )
    return sum(float(ipp[i]) * normal[i] for i in range(3))


def sort_series(paths: list[Path]) -> list[SeriesSlice]:
    """Order DICOM files into anatomical slice order.

    Falls back to ``InstanceNumber``, then filename, when position tags are absent — a
    series without geometry can still be scrolled as a stack even though it cannot be
    reconstructed into a volume.
    """
    entries: list[SeriesSlice] = []
    for path in paths:
        ds = _read_header(path)
        if ds is None:
            continue
        raw_instance = getattr(ds, "InstanceNumber", None)
        entries.append(
            SeriesSlice(
                path=path,
                position=_slice_position(ds),
                instance_number=int(raw_instance) if raw_instance is not None else None,
            )
        )

    if entries and all(e.position is not None for e in entries):
        entries.sort(key=lambda e: e.position)  # type: ignore[arg-type,return-value]
    elif entries and any(e.instance_number is not None for e in entries):
        entries.sort(key=lambda e: (e.instance_number is None, e.instance_number or 0))
    else:
        entries.sort(key=lambda e: e.path.name)
    return entries


def has_volume_geometry(paths: list[Path]) -> bool:
    """True when the series carries what a viewer needs to reslice it (MPR).

    Requires per-slice position, orientation and in-plane spacing on every file, plus at
    least two slices. Without these, sagittal and coronal views are undefined.
    """
    if len(paths) < 2:
        return False
    for path in paths:
        ds = _read_header(path)
        if ds is None:
            return False
        if getattr(ds, "ImagePositionPatient", None) is None:
            return False
        if getattr(ds, "ImageOrientationPatient", None) is None:
            return False
        if getattr(ds, "PixelSpacing", None) is None:
            return False
    return True
