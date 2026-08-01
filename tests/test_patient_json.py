"""Patient JSON parsing / schema."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from medtrace_agent.patient_json import (
    PatientJson,
    default_patient_example,
    parse_patient_json,
    patient_metadata_blob,
)


def test_default_example_roundtrip() -> None:
    ex = default_patient_example()
    raw = json.dumps(ex)
    rec = parse_patient_json(raw)
    assert rec.zep_user_id == ex["zep_user_id"]
    blob = patient_metadata_blob(rec)
    assert blob["zep_user_id"] == ex["zep_user_id"]


def test_requires_zep_user_id() -> None:
    with pytest.raises(ValidationError):
        parse_patient_json("{}")
