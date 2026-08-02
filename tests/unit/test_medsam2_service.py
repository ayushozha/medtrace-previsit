"""MedSAM2Service volumetric dispatch: mock ellipsoid, HTTP contract, legacy 2D path."""

from __future__ import annotations

from io import BytesIO

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("pydicom")
nib = pytest.importorskip("nibabel")
pytest.importorskip("PIL")

from dicom_fixtures import write_synthetic_series

SLICES, SIZE = 8, 32


class _Prompt:
    """Stand-in for apps.api.schemas.RoiPrompt (apps/ is not importable from tests)."""

    def __init__(self, x=0.25, y=0.25, width=0.5, height=0.5, slice_index=None):
        self.x, self.y, self.width, self.height = x, y, width, height
        self.slice_index = slice_index

    def model_dump(self):
        return {
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            "slice_index": self.slice_index,
        }


@pytest.fixture()
def study(tmp_path, monkeypatch):
    """A synthetic multi-slice study stored under a temp data/ root."""
    import medtrace_agent.imaging.storage as storage

    monkeypatch.setattr(storage, "_REPO_ROOT", tmp_path)
    for var in ("MEDSAM2_ENDPOINT", "MEDSAM2_ADAPTER_MODULE", "MEDSAM2_API_KEY"):
        monkeypatch.delenv(var, raising=False)

    study_id = "ST-TEST"
    write_synthetic_series(
        tmp_path / "data" / "studies" / study_id,
        slices=SLICES,
        size=SIZE,
        pixel_spacing=2.0,
        slice_spacing=3.0,
    )
    return study_id


def _service():
    from medtrace_agent.imaging.model_adapters.medsam2 import MedSAM2Service

    return MedSAM2Service()


def test_mock_volumetric_is_deterministic_and_labeled(study, tmp_path):
    result = _service().segment(study_id=study, prompt=_Prompt(slice_index=4))

    assert result["source"] == "mock"
    assert result["confidence"] == 0.0
    assert "Mock" in result["label"]
    assert result["prompt_slice"] == 4
    assert result["mask_shape"] == [SLICES, SIZE, SIZE]
    assert result["voxel_spacing_mm"] == [3.0, 2.0, 2.0]

    seg_dir = tmp_path / "data" / "studies" / study / "segmentations" / result["id"]
    first_bytes = (seg_dir / "mask.bin").read_bytes()
    mask = np.frombuffer(first_bytes, dtype="uint8").reshape(SLICES, SIZE, SIZE)

    # Ellipsoid is centred on the prompted slice and inside the drawn box.
    assert mask[4, SIZE // 2, SIZE // 2] == 1
    per_slice = mask.reshape(SLICES, -1).sum(axis=1)
    assert per_slice.argmax() == 4
    assert mask[:, : SIZE // 4 - 1, :].sum() == 0  # nothing above the box

    from medtrace_agent.imaging.volume import mask_volume_ml

    assert result["volume_ml"] == mask_volume_ml(mask, (3.0, 2.0, 2.0))

    repeat = _service().segment(study_id=study, prompt=_Prompt(slice_index=4))
    assert repeat["id"] == result["id"]
    assert (seg_dir / "mask.bin").read_bytes() == first_bytes

    urls = result["slice_overlay_urls"]
    assert len(urls) == SLICES
    assert (urls[4] or "").endswith("/slices/0004.png")
    assert result["overlay_url"] == urls[4]

    # Same prompt → same id, identical bytes.


def _npz_bytes(**arrays):
    payload = BytesIO()
    np.savez(payload, **arrays)
    return payload.getvalue()


def test_npz_mask_parser_rejects_pickle_and_unexpected_members(tmp_path):
    service = _service()

    with pytest.raises(ValueError, match="Object arrays cannot be loaded"):
        service._parse_mask_response(
            _npz_bytes(mask=np.array([[[object()]]], dtype=object)),
            tmp_path,
            (1, 1, 1),
        )

    with pytest.raises(ValueError, match="only a mask array"):
        service._parse_mask_response(
            _npz_bytes(mask=np.ones((1, 1, 1)), metadata=np.ones(1)),
            tmp_path,
            (1, 1, 1),
        )


def test_npz_mask_parser_preserves_an_exact_ambiguous_shape(tmp_path):
    mask = np.zeros((2, 3, 2), dtype="uint8")
    mask[0, 0, 1] = 1

    parsed = _service()._parse_mask_response(_npz_bytes(mask=mask), tmp_path, mask.shape)

    assert np.array_equal(parsed, mask)


def test_mask_parser_rejects_unknown_response_format(tmp_path):
    with pytest.raises(ValueError, match="neither an NPZ mask nor a gzipped NIfTI"):
        _service()._parse_mask_response(b"not-a-mask", tmp_path, (1, 1, 1))


def test_mock_defaults_to_middle_slice(study):
    result = _service().segment(study_id=study, prompt=_Prompt(slice_index=None))
    assert result["prompt_slice"] == SLICES // 2


def test_single_file_study_keeps_legacy_mock_shape(tmp_path, monkeypatch):
    import medtrace_agent.imaging.storage as storage

    monkeypatch.setattr(storage, "_REPO_ROOT", tmp_path)
    for var in ("MEDSAM2_ENDPOINT", "MEDSAM2_ADAPTER_MODULE", "MEDSAM2_API_KEY"):
        monkeypatch.delenv(var, raising=False)

    study_id = "ST-SINGLE"
    write_synthetic_series(tmp_path / "data" / "studies" / study_id, slices=1, size=16)

    result = _service().segment(study_id=study_id, prompt=_Prompt())
    assert result["source"] == "mock"
    assert result["id"] == f"seg-{study_id}"
    assert "mask_url" not in result
    assert "slice_overlay_urls" not in result


def test_http_volumetric_contract(study, tmp_path, monkeypatch):
    import httpx

    monkeypatch.setenv("MEDSAM2_ENDPOINT", "http://medsam2.internal:8080")
    monkeypatch.setenv("MEDSAM2_API_KEY", "test-key")

    captured = {}

    def fake_post(url, headers=None, files=None, data=None, timeout=None, **kwargs):
        captured.update(url=url, headers=headers, data=data)
        # Server answers on the same (cols, rows, slices) grid it was sent.
        mask = np.zeros((SIZE, SIZE, SLICES), dtype="uint8")
        mask[10:20, 12:22, 3:6] = 1
        out = tmp_path / "server_mask.nii.gz"
        nib.save(nib.Nifti1Image(mask, np.eye(4)), str(out))

        class _Response:
            content = out.read_bytes()

            @staticmethod
            def raise_for_status():
                return None

        return _Response()

    monkeypatch.setattr(httpx, "post", fake_post)

    result = _service().segment(study_id=study, prompt=_Prompt(slice_index=4))

    assert captured["url"] == "http://medsam2.internal:8080/v1/medsam2/segment/3d"
    assert captured["headers"] == {"Authorization": "Bearer test-key"}
    assert captured["data"]["key_slice_index"] == "4"
    x_min, y_min, x_max, y_max = (int(v) for v in captured["data"]["bbox"].split(","))
    assert (x_min, y_min, x_max, y_max) == (8, 8, 24, 24)  # 0.25–0.75 of a 32px image

    assert result["source"] == "medsam2"
    assert result["label"] == "MedSAM2 3D ROI"
    assert result["mask_shape"] == [SLICES, SIZE, SIZE]

    seg_dir = tmp_path / "data" / "studies" / study / "segmentations" / result["id"]
    mask = np.fromfile(seg_dir / "mask.bin", dtype="uint8").reshape(SLICES, SIZE, SIZE)
    # (cols, rows, slices) from the server transposes back to [D, H, W].
    assert mask.sum() == 10 * 10 * 3
    assert mask[3:6, 12:22, 10:20].sum() == mask.sum()
    from medtrace_agent.imaging.volume import mask_volume_ml

    assert result["volume_ml"] == mask_volume_ml(mask, (3.0, 2.0, 2.0))
