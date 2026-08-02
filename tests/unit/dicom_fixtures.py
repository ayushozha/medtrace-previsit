"""Synthetic DICOM series for imaging tests.

A miniature of ``scripts/make_phantom_series.py``: a tiny axial CT series with full 3D
geometry (ImagePositionPatient / ImageOrientationPatient / PixelSpacing) so volume
assembly, NIfTI export and MPR gating can be exercised without real patient data.
Spacing is deliberately anisotropic so axis mix-ups fail loudly in spacing assertions.
"""

from __future__ import annotations

from pathlib import Path


def write_synthetic_series(
    out_dir: Path,
    *,
    slices: int = 8,
    size: int = 32,
    pixel_spacing: float = 2.0,
    slice_spacing: float = 3.0,
) -> list[Path]:
    """Write ``slices`` single-frame CT files and return their paths in slice order.

    Voxel values encode their own slice index (slice ``i`` is filled with HU value ``i``)
    so tests can verify sort order from pixel data alone.
    """
    import numpy as np
    import pydicom  # noqa: F401 — asserts the dependency before building datasets
    from pydicom.dataset import FileDataset, FileMetaDataset
    from pydicom.uid import CTImageStorage, ExplicitVRLittleEndian, generate_uid

    out_dir.mkdir(parents=True, exist_ok=True)
    study_uid, series_uid, frame_uid = generate_uid(), generate_uid(), generate_uid()

    paths: list[Path] = []
    for i in range(slices):
        meta = FileMetaDataset()
        meta.MediaStorageSOPClassUID = CTImageStorage
        meta.MediaStorageSOPInstanceUID = generate_uid()
        meta.TransferSyntaxUID = ExplicitVRLittleEndian

        ds = FileDataset(None, {}, file_meta=meta, preamble=b"\0" * 128)
        ds.SOPClassUID = CTImageStorage
        ds.SOPInstanceUID = meta.MediaStorageSOPInstanceUID
        ds.StudyInstanceUID, ds.SeriesInstanceUID = study_uid, series_uid
        ds.FrameOfReferenceUID = frame_uid

        ds.PatientName = "Fixture^Synthetic"
        ds.PatientID = "FIXTURE-001"
        ds.Modality = "CT"
        ds.SeriesDescription = "Synthetic test series"
        ds.InstanceNumber = i + 1

        ds.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
        ds.ImagePositionPatient = [
            -size * pixel_spacing / 2,
            -size * pixel_spacing / 2,
            i * slice_spacing,
        ]
        ds.PixelSpacing = [pixel_spacing, pixel_spacing]
        ds.SliceThickness = slice_spacing
        ds.SpacingBetweenSlices = slice_spacing

        ds.Rows = ds.Columns = size
        ds.SamplesPerPixel = 1
        ds.PhotometricInterpretation = "MONOCHROME2"
        ds.BitsAllocated = 16
        ds.BitsStored = 16
        ds.HighBit = 15
        ds.PixelRepresentation = 1
        ds.RescaleSlope = 1
        ds.RescaleIntercept = 0

        ds.PixelData = np.full((size, size), i, dtype=np.int16).tobytes()
        path = out_dir / f"slice_{i:04d}.dcm"
        ds.save_as(path, enforce_file_format=True)
        paths.append(path)
    return paths
