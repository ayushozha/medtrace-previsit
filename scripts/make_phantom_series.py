#!/usr/bin/env python3
"""Generate a synthetic CT series with correct 3D geometry, for testing MPR.

The pydicom sample files carry no ``ImagePositionPatient`` / ``ImageOrientationPatient`` /
``PixelSpacing``, so a viewer cannot place their slices in space and multiplanar
reconstruction is undefined. This writes a proper axial series where those tags are set,
which is what makes reslicing into sagittal and coronal views possible.

The phantom is deliberately asymmetric so each plane is visually distinguishable, and
contains a **sphere** — the geometry check is that the sphere reads as a circle of the same
diameter in all three planes. If spacing or orientation is wrong it renders as an ellipse.

Usage::

    .venv/bin/python scripts/make_phantom_series.py                 # -> data/phantom_ct/
    .venv/bin/python scripts/make_phantom_series.py --slices 120 --size 256
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]

# Isotropic-ish CT geometry: 1 mm in plane, 1 mm between slices, so a sphere stays a sphere.
PIXEL_SPACING = 1.0
SLICE_SPACING = 1.0


def build_volume(size: int, slices: int):
    """Hounsfield-unit volume: soft-tissue background, bone shell, sphere, and a rod."""
    import numpy as np

    zz, yy, xx = np.mgrid[0:slices, 0:size, 0:size].astype("float32")
    cz, cy, cx = slices / 2, size / 2, size / 2

    vol = np.full((slices, size, yy.shape[2]), -1000.0, dtype="float32")  # air

    # Body: elliptical cylinder of soft tissue (wider than tall — orients coronal vs sagittal).
    body = ((xx - cx) / (size * 0.40)) ** 2 + ((yy - cy) / (size * 0.30)) ** 2
    vol[body <= 1.0] = 40.0

    # Bone shell just inside the body wall.
    vol[(body <= 1.0) & (body >= 0.86)] = 900.0

    # The geometry probe: a centred sphere. Size it against the *smallest* axis (usually the
    # stack depth) so it is never clipped — a clipped sphere would look like a failed
    # reconstruction rather than a failed phantom.
    r_sphere = min(size, slices) * 0.28
    sphere = (xx - cx) ** 2 + (yy - cy) ** 2 + ((zz - cz) * (SLICE_SPACING / PIXEL_SPACING)) ** 2
    vol[sphere <= r_sphere**2] = 300.0

    # An off-centre rod running head-to-foot: only ever a circle on axial, a line on the others.
    rod = (xx - cx * 1.45) ** 2 + (yy - cy * 0.62) ** 2
    vol[rod <= (size * 0.06) ** 2] = 1200.0

    return vol


def main() -> int:
    parser = argparse.ArgumentParser(description="Write a synthetic CT series with real geometry.")
    parser.add_argument("--slices", type=int, default=120, help="Number of axial slices.")
    parser.add_argument("--size", type=int, default=256, help="In-plane matrix size.")
    parser.add_argument(
        "--out",
        type=Path,
        default=_REPO / "data" / "phantom_ct",
        help="Output directory (recreated).",
    )
    args = parser.parse_args()

    try:
        import numpy as np
        import pydicom
        from pydicom.dataset import FileDataset, FileMetaDataset
        from pydicom.uid import CTImageStorage, ExplicitVRLittleEndian, generate_uid
    except ImportError as exc:
        print(f"Needs the imaging extra: pip install -e '.[imaging]'  ({exc})", file=sys.stderr)
        return 1

    out: Path = args.out
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    vol = build_volume(args.size, args.slices)
    study_uid, series_uid, frame_uid = generate_uid(), generate_uid(), generate_uid()

    for i in range(args.slices):
        meta = FileMetaDataset()
        meta.MediaStorageSOPClassUID = CTImageStorage
        meta.MediaStorageSOPInstanceUID = generate_uid()
        meta.TransferSyntaxUID = ExplicitVRLittleEndian

        ds = FileDataset(None, {}, file_meta=meta, preamble=b"\0" * 128)
        ds.SOPClassUID = CTImageStorage
        ds.SOPInstanceUID = meta.MediaStorageSOPInstanceUID
        ds.StudyInstanceUID, ds.SeriesInstanceUID = study_uid, series_uid
        ds.FrameOfReferenceUID = frame_uid

        ds.PatientName = "Phantom^Synthetic"
        ds.PatientID = "PHANTOM-001"
        ds.Modality = "CT"
        ds.SeriesDescription = "Synthetic MPR phantom"
        ds.BodyPartExamined = "PHANTOM"
        ds.StudyDate = ds.SeriesDate = "20260801"
        ds.InstanceNumber = i + 1

        # --- the geometry that makes MPR possible -------------------------------
        # Axial acquisition: rows run left→right (1,0,0), columns run anterior→posterior (0,1,0).
        ds.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
        # Each slice steps along +z, so the stack has a real physical extent.
        ds.ImagePositionPatient = [
            -args.size * PIXEL_SPACING / 2,
            -args.size * PIXEL_SPACING / 2,
            i * SLICE_SPACING,
        ]
        ds.PixelSpacing = [PIXEL_SPACING, PIXEL_SPACING]
        ds.SliceThickness = SLICE_SPACING
        ds.SpacingBetweenSlices = SLICE_SPACING
        ds.SliceLocation = i * SLICE_SPACING
        # ------------------------------------------------------------------------

        ds.Rows, ds.Columns = args.size, args.size
        ds.SamplesPerPixel = 1
        ds.PhotometricInterpretation = "MONOCHROME2"
        ds.BitsAllocated = 16
        ds.BitsStored = 16
        ds.HighBit = 15
        ds.PixelRepresentation = 1  # signed, so negative HU survives
        # Store raw HU: slope 1 / intercept 0 keeps the values honest.
        ds.RescaleSlope = 1
        ds.RescaleIntercept = 0
        ds.RescaleType = "HU"
        ds.WindowCenter = 300
        ds.WindowWidth = 1500

        ds.PixelData = vol[i].astype(np.int16).tobytes()
        ds.save_as(out / f"slice_{i:04d}.dcm", enforce_file_format=True)

    total = sum(f.stat().st_size for f in out.glob("*.dcm"))
    print(f"Wrote {args.slices} slices ({args.size}x{args.size}) to {out}")
    print(f"  {total / 1e6:.1f} MB · {PIXEL_SPACING} mm in-plane · {SLICE_SPACING} mm slice spacing")
    print("  Sphere should read as the same-diameter circle on axial, sagittal and coronal.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
