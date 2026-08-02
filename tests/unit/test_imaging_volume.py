"""Volume assembly, NIfTI round-trip, and mask persistence."""

from __future__ import annotations

import json

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("pydicom")
pytest.importorskip("nibabel")
pytest.importorskip("PIL")

from dicom_fixtures import write_synthetic_series

SLICES, SIZE = 8, 32
PIXEL_SPACING, SLICE_SPACING = 2.0, 3.0


@pytest.fixture()
def series_paths(tmp_path):
    return write_synthetic_series(
        tmp_path / "series",
        slices=SLICES,
        size=SIZE,
        pixel_spacing=PIXEL_SPACING,
        slice_spacing=SLICE_SPACING,
    )


def test_load_series_volume_shape_spacing_and_order(series_paths):
    from medtrace_agent.imaging.volume import load_series_volume

    # Scramble the input order — anatomy must come from geometry, not the file list.
    scrambled = list(reversed(series_paths[1::2])) + series_paths[::2]
    volume, spacing = load_series_volume(scrambled)

    assert volume.shape == (SLICES, SIZE, SIZE)
    assert spacing == (SLICE_SPACING, PIXEL_SPACING, PIXEL_SPACING)
    # Each fixture slice is filled with its own index.
    for i in range(SLICES):
        assert float(volume[i].mean()) == pytest.approx(float(i))


def test_nifti_round_trip_preserves_voxels_and_spacing(series_paths, tmp_path):
    import nibabel as nib

    from medtrace_agent.imaging.volume import load_nifti_mask, series_to_nifti

    nii_path = tmp_path / "volume.nii.gz"
    volume, spacing = series_to_nifti(series_paths, nii_path)

    img = nib.load(str(nii_path))
    assert img.shape == (SIZE, SIZE, SLICES)  # (cols, rows, slices) NIfTI layout
    zooms = img.header.get_zooms()
    assert zooms[0] == pytest.approx(PIXEL_SPACING)
    assert zooms[1] == pytest.approx(PIXEL_SPACING)
    assert zooms[2] == pytest.approx(SLICE_SPACING)
    data = np.asanyarray(img.dataobj)
    assert np.array_equal(data.transpose(2, 1, 0), volume)

    # A mask written on the NIfTI grid must come back in [D, H, W] order.
    mask_nifti = (data > 2).astype("uint8")
    mask_path = tmp_path / "mask.nii.gz"
    nib.save(nib.Nifti1Image(mask_nifti, img.affine), str(mask_path))
    mask = load_nifti_mask(mask_path, expected_shape=volume.shape)
    assert mask.shape == (SLICES, SIZE, SIZE)
    assert np.array_equal(mask, (volume > 2).astype("uint8"))


def test_load_nifti_mask_rejects_wrong_grid(series_paths, tmp_path):
    import nibabel as nib

    from medtrace_agent.imaging.volume import load_nifti_mask

    wrong = nib.Nifti1Image(np.zeros((4, 5, 6), dtype="uint8"), np.eye(4))
    path = tmp_path / "wrong.nii.gz"
    nib.save(wrong, str(path))
    with pytest.raises(ValueError, match="does not match"):
        load_nifti_mask(path, expected_shape=(SLICES, SIZE, SIZE))


def test_mask_volume_ml_matches_analytic_value():
    from medtrace_agent.imaging.volume import mask_volume_ml

    mask = np.zeros((4, 10, 10), dtype="uint8")
    mask[1:3, 2:7, 3:9] = 1  # 2 * 5 * 6 = 60 voxels
    # voxel = 3.0 * 2.0 * 2.0 = 12 mm³ → 60 * 12 / 1000 = 0.72 mL
    assert mask_volume_ml(mask, (3.0, 2.0, 2.0)) == pytest.approx(0.72)


def test_save_mask_artifacts_round_trip(tmp_path, monkeypatch):
    import medtrace_agent.imaging.storage as storage
    from medtrace_agent.imaging.masks import save_mask_artifacts

    monkeypatch.setattr(storage, "_REPO_ROOT", tmp_path)

    mask = np.zeros((6, 16, 16), dtype="uint8")
    mask[2:5, 4:12, 5:11] = 1
    result = save_mask_artifacts("ST-TEST", "seg-1", mask, (3.0, 2.0, 2.0), prompt_slice=3)

    seg_dir = tmp_path / "data" / "studies" / "ST-TEST" / "segmentations" / "seg-1"
    stored = np.fromfile(seg_dir / "mask.bin", dtype="uint8").reshape(6, 16, 16)
    assert np.array_equal(stored, mask)

    meta = json.loads((seg_dir / "meta.json").read_text())
    assert meta["shape"] == [6, 16, 16]
    assert meta["spacing_mm"] == [3.0, 2.0, 2.0]
    assert meta["prompt_slice"] == 3
    assert meta["voxel_count"] == 3 * 8 * 6
    assert result["volume_ml"] == meta["volume_ml"]

    # Overlays exist exactly where the mask has voxels: slices 2..4.
    urls = result["slice_overlay_urls"]
    assert len(urls) == 6
    assert [u is not None for u in urls] == [False, False, True, True, True, False]
    assert (seg_dir / "slices" / "0003.png").is_file()
    assert not (seg_dir / "slices" / "0000.png").exists()
    assert result["overlay_url"] == urls[3]
    assert result["mask_url"].endswith("/segmentations/seg-1/mask.bin")


def test_study_series_paths_skips_non_dicom(tmp_path, monkeypatch):
    import medtrace_agent.imaging.storage as storage
    from medtrace_agent.imaging.volume import study_series_paths

    monkeypatch.setattr(storage, "_REPO_ROOT", tmp_path)
    study = tmp_path / "data" / "studies" / "ST-TEST"
    write_synthetic_series(study, slices=3, size=16)
    (study / "preview.png").write_bytes(b"not a dicom")
    (study / "segmentations").mkdir()

    paths = study_series_paths("ST-TEST")
    assert [p.name for p in paths] == ["slice_0000.dcm", "slice_0001.dcm", "slice_0002.dcm"]
