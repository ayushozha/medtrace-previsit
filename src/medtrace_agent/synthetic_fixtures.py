"""Neutral access to committed synthetic fixtures and historical JSON exports."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from medtrace_agent.patient_json import derive_age, derive_primary_doctor


_REPO_ROOT = Path(__file__).resolve().parents[2]
_MOCK_PATIENTS_DIR = _REPO_ROOT / "mock" / "patient_data"
_IMPORT_PATH = _REPO_ROOT / "data" / "local_mock" / "store.json"
_IMPORT_PROFILE_ID = "00000000-0000-4000-8000-000000000001"


def _clinical_fixture(index: int) -> dict[str, Any]:
    """Return deterministic synthetic clinical facts for importer smoke tests."""
    if index == 0:
        return {
            "conditions": [
                {"name": "Type 2 diabetes", "status": "Active"},
                {"name": "Hypertension", "status": "Active"},
                {"name": "Hyperlipidemia", "status": "Active"},
            ],
            "medications": [
                {"name": "Metformin", "dose": "500 mg", "frequency": "twice daily", "status": "Active"},
                {"name": "Lisinopril", "dose": "10 mg", "frequency": "once daily", "status": "Active"},
                {"name": "Atorvastatin", "dose": "20 mg", "frequency": "once daily", "status": "Previous"},
            ],
            "allergies": [{"allergen": "Penicillin", "reaction": "Rash"}],
            "labs": [
                {"test": "HbA1c", "latest": "8.4%", "previous": "7.1%", "status": "High", "range": "< 7.0%"},
                {"test": "LDL", "latest": "150 mg/dL", "previous": "135 mg/dL", "status": "High", "range": "< 100 mg/dL"},
                {"test": "Creatinine", "latest": "1.0 mg/dL", "previous": "0.9 mg/dL", "status": "Normal", "range": "0.7-1.3 mg/dL"},
            ],
        }
    if index % 3 == 2:
        return {
            "conditions": [{"name": "Type 2 diabetes", "status": "Active"}],
            "medications": [
                {"name": "Metformin", "dose": "1000 mg", "frequency": "twice daily", "status": "Active"}
            ],
            "allergies": [],
            "labs": [
                {"test": "HbA1c", "latest": "7.8%", "previous": "7.4%", "status": "High", "range": "< 7.0%"}
            ],
        }
    if index % 3 == 0:
        return {
            "conditions": [{"name": "Hepatic lesion (under evaluation)", "status": "Active"}],
            "medications": [],
            "allergies": [{"allergen": "Iodinated contrast", "reaction": "Mild urticaria"}],
            "labs": [],
        }
    return {"conditions": [], "medications": [], "allergies": [], "labs": []}


def _fallback_store() -> dict[str, Any]:
    """Build the import shape directly from committed JSON without a runtime database twin."""
    records = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(_MOCK_PATIENTS_DIR.glob("patient_*.json"))]
    charts: list[dict[str, Any]] = []
    documents: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        zep_user_id = str(record["zep_user_id"])
        chart_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"medtrace-import:{zep_user_id}"))
        demographics = record.get("demographics") if isinstance(record.get("demographics"), dict) else {}
        charts.append({
            "id": chart_id,
            "owner_profile_id": _IMPORT_PROFILE_ID,
            "zep_user_id": zep_user_id,
            "display_name": str(record.get("display_name") or f"Synthetic Patient {index + 1}"),
            "metadata": {
                "fields": {
                    "age": derive_age(demographics.get("age_band")),
                    "sex": ("M", "F", "O")[index % 3],
                    "dob": None,
                    "primary_doctor": derive_primary_doctor(record),
                    "notes": record.get("notes"),
                    "tags": list(record.get("tags") or ["synthetic"]),
                },
                "clinical": _clinical_fixture(index),
            },
            "created_at": f"2026-04-{max(1, 30 - index):02d}T12:00:00+00:00",
        })
        if index == 0:
            for filename, episodes, uploaded in (
                ("Lab Report May 2026.pdf", 12, "2026-05-09T15:00:00+00:00"),
                ("Prescription Jan 2026.pdf", 7, "2026-02-01T12:00:00+00:00"),
                ("Discharge Summary 2025.pdf", 18, "2025-12-20T18:00:00+00:00"),
            ):
                source_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{chart_id}:{filename}"))
                documents.append({
                    "id": source_id,
                    "doc_id": source_id,
                    "profile_id": _IMPORT_PROFILE_ID,
                    "chart_subject_id": chart_id,
                    "filename": filename,
                    "document_kind": "clinical_pdf",
                    "extract_mode": "synthetic-placeholder",
                    "episode_count": episodes,
                    "storage_key": "",
                    "metadata": {"synthetic_placeholder": True},
                    "uploaded_at": uploaded,
                })
    return {
        "profile_id": _IMPORT_PROFILE_ID,
        "chart_subjects": charts,
        "documents": documents,
        "chat_sessions": [],
    }


def load_synthetic_store(path: Path | None = None) -> dict[str, Any]:
    """Read a historical JSON export, falling back to committed synthetic fixtures."""
    store_path = path or _IMPORT_PATH
    if store_path.is_file():
        data = json.loads(store_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"Historical import file must contain a JSON object: {store_path}")
        return data
    return _fallback_store()
