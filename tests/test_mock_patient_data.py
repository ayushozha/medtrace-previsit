"""Ensure mock/patient_data/*.json validates against PatientJson."""

from __future__ import annotations

import json
from pathlib import Path

from medtrace_agent.patient_json import parse_patient_json

_MOCK_DIR = Path(__file__).resolve().parents[1] / "mock" / "patient_data"


def test_all_mock_patients_validate() -> None:
    files = sorted(_MOCK_DIR.glob("patient_*.json"))
    assert len(files) == 10, f"expected 10 patient_*.json, got {len(files)}"
    for path in files:
        raw = path.read_text(encoding="utf-8")
        rec = parse_patient_json(raw)
        assert rec.zep_user_id.startswith("synthetic-mock-pt-")
        meta = json.loads(raw)
        assert meta["zep_user_id"] == rec.zep_user_id
