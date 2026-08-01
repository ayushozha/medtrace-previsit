"""File-backed local stand-in for InsForge (patients / documents / chat sessions).

Enable with ``MEDTRACE_LOCAL_MOCK=1`` in the root ``.env``. Data lives under
``data/local_mock/store.json`` (created on first use and auto-seeded from
``mock/patient_data/*.json`` plus rich clinical fixtures for the dashboard).
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from medtrace_agent.patient_json import derive_age, derive_primary_doctor

DocumentKind = Literal["clinical_pdf", "radiology_note", "conversation_note"]

LOCAL_PROFILE_ID = "00000000-0000-4000-8000-000000000001"
_REPO_ROOT = Path(__file__).resolve().parents[2]
_STORE_DIR = _REPO_ROOT / "data" / "local_mock"
_STORE_PATH = _STORE_DIR / "store.json"
_FILES_DIR = _STORE_DIR / "files"
_MOCK_PATIENTS_DIR = _REPO_ROOT / "mock" / "patient_data"


def local_mock_enabled() -> bool:
    raw = (os.environ.get("MEDTRACE_LOCAL_MOCK") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def local_profile_id() -> str:
    return (os.environ.get("INSFORGE_PROFILE_ID") or "").strip() or LOCAL_PROFILE_ID


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _empty_store() -> dict[str, Any]:
    return {
        "profile_id": local_profile_id(),
        "chart_subjects": [],
        "documents": [],
        "chat_sessions": [],
    }


def _load() -> dict[str, Any]:
    _STORE_DIR.mkdir(parents=True, exist_ok=True)
    _FILES_DIR.mkdir(parents=True, exist_ok=True)
    if not _STORE_PATH.is_file():
        store = _seed_store()
        _save(store)
        return store
    try:
        data = json.loads(_STORE_PATH.read_text(encoding="utf-8"))
    except Exception:
        data = _seed_store()
        _save(data)
        return data
    if not isinstance(data, dict):
        data = _seed_store()
        _save(data)
        return data
    data.setdefault("chart_subjects", [])
    data.setdefault("documents", [])
    data.setdefault("chat_sessions", [])
    data.setdefault("profile_id", local_profile_id())
    if not data["chart_subjects"]:
        data = _seed_store()
        _save(data)
    return data


def _save(store: dict[str, Any]) -> None:
    _STORE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _STORE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(store, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(_STORE_PATH)


def _clinical_for_index(i: int, display_name: str) -> dict[str, Any]:
    """Rich dashboard fixtures. Index 0 gets the full John-Doe-style chronic-care chart."""
    if i == 0:
        return {
            "summary": (
                f"{display_name} has a history of type 2 diabetes, hypertension, and high cholesterol. "
                "Recent records show worsening blood sugar control and continued elevated blood pressure. "
                "Current medications include Metformin and Lisinopril. A penicillin allergy is documented."
            ),
            "risk_level": "High",
            "condition_count": 3,
            "last_visit": "2026-05-09",
            "doctor_checklist": [
                "Review elevated HbA1c trend",
                "Confirm current diabetes medication adherence",
                "Check if patient is still taking Lisinopril",
                "Review documented penicillin allergy before prescribing antibiotics",
            ],
            "insights": [
                {
                    "title": "Worsening glucose control",
                    "detail": "HbA1c rose from 7.1% to 8.4% across uploaded lab reports.",
                    "evidence": [
                        "Lab Report Jan 2026: HbA1c 7.1%",
                        "Lab Report May 2026: HbA1c 8.4%",
                    ],
                    "priority": "High",
                },
                {
                    "title": "Persistent elevated blood pressure",
                    "detail": "Blood pressure remains elevated across the last three clinical records.",
                    "evidence": ["Visit note Jan 2026: 145/90", "Visit Summary May 2026: 148/92"],
                    "priority": "Medium",
                },
                {
                    "title": "Medication continuity check",
                    "detail": "Latest document still lists Metformin and Lisinopril; Atorvastatin appears stopped.",
                    "evidence": ["Prescription Jan 2026", "Discharge Summary Dec 2025"],
                    "priority": "Low",
                },
            ],
            "clinical": {
                "conditions": [
                    {
                        "name": "Type 2 diabetes",
                        "status": "Active",
                        "first_seen": "Jan 2025",
                        "last_mentioned": "May 2026",
                    },
                    {
                        "name": "Hypertension",
                        "status": "Active",
                        "first_seen": "Mar 2025",
                        "last_mentioned": "May 2026",
                    },
                    {
                        "name": "Hyperlipidemia",
                        "status": "Active",
                        "first_seen": "Jan 2025",
                        "last_mentioned": "Dec 2025",
                    },
                ],
                "medications": [
                    {
                        "name": "Metformin",
                        "dose": "500 mg",
                        "frequency": "twice daily",
                        "status": "Active",
                        "start": "Jan 2025",
                        "end": None,
                    },
                    {
                        "name": "Lisinopril",
                        "dose": "10 mg",
                        "frequency": "once daily",
                        "status": "Active",
                        "start": "Mar 2025",
                        "end": None,
                    },
                    {
                        "name": "Atorvastatin",
                        "dose": "20 mg",
                        "frequency": "once daily",
                        "status": "Previous",
                        "start": "Jan 2025",
                        "end": "Dec 2025",
                    },
                ],
                "allergies": [
                    {"allergen": "Penicillin", "reaction": "Rash", "source": "Intake Form"}
                ],
                "recent_abnormal": [
                    {
                        "test": "HbA1c",
                        "value": "8.4%",
                        "status": "high",
                        "source": "Lab Report May 2026",
                    },
                    {
                        "test": "Blood pressure",
                        "value": "148/92",
                        "status": "elevated",
                        "source": "Visit Summary May 2026",
                    },
                ],
                "alerts": [
                    {
                        "message": "Documented penicillin allergy",
                        "priority": "High",
                        "type": "allergy",
                        "evidence": "Intake Form 2025",
                    },
                    {
                        "message": "HbA1c increased over the last six months",
                        "priority": "High",
                        "type": "value_trend",
                        "evidence": "Jan and May lab reports",
                    },
                    {
                        "message": "Blood pressure remains elevated across multiple records",
                        "priority": "Medium",
                        "type": "value_trend",
                        "evidence": "Three recent visit notes",
                    },
                ],
                "labs": [
                    {
                        "test": "HbA1c",
                        "latest": "8.4%",
                        "previous": "7.1%",
                        "status": "High",
                        "trend": "Worsening",
                        "date": "May 2026",
                        "range": "< 7.0%",
                        "source": "Lab Report May 2026",
                    },
                    {
                        "test": "LDL",
                        "latest": "150 mg/dL",
                        "previous": "135 mg/dL",
                        "status": "High",
                        "trend": "Worsening",
                        "date": "May 2026",
                        "range": "< 100 mg/dL",
                        "source": "Lab Report May 2026",
                    },
                    {
                        "test": "Creatinine",
                        "latest": "1.0 mg/dL",
                        "previous": "0.9 mg/dL",
                        "status": "Normal",
                        "trend": "Stable",
                        "date": "May 2026",
                        "range": "0.7-1.3 mg/dL",
                        "source": "Lab Report May 2026",
                    },
                ],
                "timeline": [
                    {
                        "date": "Jan 2025",
                        "events": [
                            "Diagnosed with type 2 diabetes",
                            "Started Metformin",
                            "Started Atorvastatin",
                        ],
                    },
                    {
                        "date": "Mar 2025",
                        "events": ["Diagnosed with Hypertension", "Started Lisinopril"],
                    },
                    {"date": "Jun 2025", "events": ["HbA1c improved to 7.1%"]},
                    {"date": "Dec 2025", "events": ["Stopped Atorvastatin"]},
                    {"date": "Jan 2026", "events": ["Blood pressure elevated at 145/90"]},
                    {
                        "date": "May 2026",
                        "events": ["HbA1c increased to 8.4%", "Latest lab report uploaded"],
                    },
                ],
            },
        }

    light = [
        {
            "summary": f"{display_name}: routine wellness follow-up. No acute concerns on chart review.",
            "risk_level": "Low",
            "condition_count": 0,
            "last_visit": "2026-04-12",
            "clinical": {
                "conditions": [],
                "medications": [],
                "allergies": [],
                "recent_abnormal": [],
                "alerts": [],
                "labs": [],
                "timeline": [
                    {"date": "Apr 2026", "events": ["Annual wellness visit", "Labs within normal limits"]}
                ],
            },
            "doctor_checklist": ["Confirm preventive screening schedule"],
            "insights": [],
        },
        {
            "summary": f"{display_name}: medication-adherence discussion in progress for chronic care.",
            "risk_level": "Medium",
            "condition_count": 1,
            "last_visit": "2026-05-01",
            "clinical": {
                "conditions": [
                    {
                        "name": "Type 2 diabetes",
                        "status": "Active",
                        "first_seen": "2024",
                        "last_mentioned": "May 2026",
                    }
                ],
                "medications": [
                    {
                        "name": "Metformin",
                        "dose": "1000 mg",
                        "frequency": "twice daily",
                        "status": "Active",
                        "start": "2024",
                        "end": None,
                    }
                ],
                "allergies": [],
                "recent_abnormal": [
                    {
                        "test": "HbA1c",
                        "value": "7.8%",
                        "status": "high",
                        "source": "Clinic note May 2026",
                    }
                ],
                "alerts": [
                    {
                        "message": "Possible missed doses reported by patient",
                        "priority": "Medium",
                        "type": "medication",
                        "evidence": "Visit note May 2026",
                    }
                ],
                "labs": [
                    {
                        "test": "HbA1c",
                        "latest": "7.8%",
                        "previous": "7.4%",
                        "status": "High",
                        "trend": "Worsening",
                        "date": "May 2026",
                        "range": "< 7.0%",
                        "source": "Clinic note",
                    }
                ],
                "timeline": [
                    {"date": "2024", "events": ["Started Metformin"]},
                    {"date": "May 2026", "events": ["Discussed adherence barriers"]},
                ],
            },
            "doctor_checklist": ["Review adherence plan", "Consider once-daily alternative"],
            "insights": [
                {
                    "title": "Adherence risk",
                    "detail": "Patient reported missed evening doses this month.",
                    "evidence": ["Visit note May 2026"],
                    "priority": "Medium",
                }
            ],
        },
        {
            "summary": f"{display_name}: radiology follow-up pathway for imaging review demos.",
            "risk_level": "Medium",
            "condition_count": 1,
            "last_visit": "2026-03-22",
            "clinical": {
                "conditions": [
                    {
                        "name": "Hepatic lesion (under evaluation)",
                        "status": "Active",
                        "first_seen": "Mar 2026",
                        "last_mentioned": "Mar 2026",
                    }
                ],
                "medications": [],
                "allergies": [
                    {"allergen": "Iodinated contrast", "reaction": "Mild urticaria", "source": "Prior CT"}
                ],
                "recent_abnormal": [],
                "alerts": [
                    {
                        "message": "Documented iodinated contrast allergy",
                        "priority": "High",
                        "type": "allergy",
                        "evidence": "Prior CT note",
                    }
                ],
                "labs": [],
                "timeline": [
                    {"date": "Mar 2026", "events": ["MRI ordered", "Contrast allergy flagged"]},
                ],
            },
            "doctor_checklist": ["Confirm contrast allergy protocol", "Review MRI draft report"],
            "insights": [],
        },
    ]
    return light[(i - 1) % len(light)]


def _demo_documents(chart_id: str, profile_id: str, i: int) -> list[dict[str, Any]]:
    if i != 0:
        return []
    now = _now()
    specs = [
        ("Lab Report May 2026.pdf", "clinical_pdf", 12, "2026-05-09T15:00:00+00:00"),
        ("Prescription Jan 2026.pdf", "clinical_pdf", 7, "2026-02-01T12:00:00+00:00"),
        ("Discharge Summary 2025.pdf", "clinical_pdf", 18, "2025-12-20T18:00:00+00:00"),
    ]
    rows: list[dict[str, Any]] = []
    for filename, kind, episodes, uploaded in specs:
        doc_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{chart_id}:{filename}"))
        key = f"{chart_id}/{doc_id}/{filename}"
        rows.append(
            {
                "id": str(uuid.uuid4()),
                "doc_id": doc_id,
                "profile_id": profile_id,
                "chart_subject_id": chart_id,
                "filename": filename,
                "document_kind": kind,
                "extract_mode": "local_fixture",
                "episode_count": episodes,
                "storage_bucket": "local-mock",
                "storage_key": key,
                "storage_url": None,
                "metadata": {"local_fixture": True},
                "uploaded_at": uploaded or now,
            }
        )
    return rows


def _seed_store() -> dict[str, Any]:
    profile_id = local_profile_id()
    store = _empty_store()
    store["profile_id"] = profile_id
    files = sorted(_MOCK_PATIENTS_DIR.glob("patient_*.json"))
    if not files:
        # Minimal fallback if mock JSON folder is missing.
        files = []
        synthetic = [
            {
                "zep_user_id": "local-mock-pt-001",
                "display_name": "Jordan Vale",
                "demographics": {"age_band": "40-49"},
                "notes": "Local mock seed",
                "tags": ["local-mock"],
            }
        ]
    else:
        synthetic = [json.loads(p.read_text(encoding="utf-8")) for p in files]

    for i, record in enumerate(synthetic):
        zep_user_id = str(record.get("zep_user_id") or f"local-mock-pt-{i+1:03d}")
        display_name = str(record.get("display_name") or f"Demo Patient {i+1}")
        demo = record.get("demographics") if isinstance(record.get("demographics"), dict) else {}
        age = derive_age(demo.get("age_band") if isinstance(demo, dict) else None)
        sex = "O"
        # Alternate sexes for directory variety (demo only).
        if i % 3 == 0:
            sex = "M"
        elif i % 3 == 1:
            sex = "F"
        fixture = _clinical_for_index(i, display_name)
        chart_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"local-mock:{zep_user_id}"))
        # Prefer the rich chronic-care chart (index 0) at the top of the directory.
        created = (
            "2026-05-09T18:00:00+00:00"
            if i == 0
            else f"2026-04-{max(1, 30 - i):02d}T12:00:00+00:00"
        )
        metadata = {
            "fields": {
                "age": age,
                "sex": sex,
                "dob": None,
                "primary_doctor": derive_primary_doctor(record),
                "last_visit": fixture.get("last_visit"),
                "notes": record.get("notes"),
                "tags": record.get("tags") or ["local-mock"],
            },
            "summary": fixture.get("summary"),
            "risk_level": fixture.get("risk_level") or "Low",
            "condition_count": fixture.get("condition_count") or 0,
            "doctor_checklist": fixture.get("doctor_checklist") or [],
            "insights": fixture.get("insights") or [],
            "clinical": fixture.get("clinical") or {},
            "created_via": "local_mock",
            "source_patient_json": record,
        }
        store["chart_subjects"].append(
            {
                "id": chart_id,
                "owner_profile_id": profile_id,
                "zep_user_id": zep_user_id,
                "display_name": display_name,
                "metadata": metadata,
                "created_at": created,
            }
        )
        store["documents"].extend(_demo_documents(chart_id, profile_id, i))
    return store


def reset_and_reseed() -> dict[str, Any]:
    """Wipe store.json and rebuild from fixtures (keeps uploaded files)."""
    store = _seed_store()
    _save(store)
    return store


# ---- API-shaped helpers (mirrors insforge_api) --------------------------------


def list_chart_subjects(*, profile_id: str | None = None) -> list[dict[str, Any]]:
    store = _load()
    pid = profile_id or local_profile_id()
    rows = [r for r in store["chart_subjects"] if r.get("owner_profile_id") == pid]
    return sorted(rows, key=lambda r: str(r.get("created_at") or ""), reverse=True)


def get_chart_subject(*, chart_subject_id: str) -> dict[str, Any] | None:
    store = _load()
    pid = local_profile_id()
    for row in store["chart_subjects"]:
        if str(row.get("id")) == chart_subject_id and row.get("owner_profile_id") == pid:
            return row
    return None


def ensure_chart_subject_id(
    *,
    zep_user_id: str,
    display_name: str,
    metadata: dict[str, Any] | None = None,
) -> str | None:
    store = _load()
    pid = local_profile_id()
    meta = metadata if metadata is not None else {}
    for row in store["chart_subjects"]:
        if row.get("owner_profile_id") == pid and row.get("zep_user_id") == zep_user_id:
            row["display_name"] = display_name or row.get("display_name")
            row["metadata"] = meta
            _save(store)
            return str(row["id"])
    chart_id = str(uuid.uuid4())
    store["chart_subjects"].append(
        {
            "id": chart_id,
            "owner_profile_id": pid,
            "zep_user_id": zep_user_id,
            "display_name": display_name or None,
            "metadata": meta,
            "created_at": _now(),
        }
    )
    _save(store)
    return chart_id


def update_chart_subject_metadata(
    *,
    chart_subject_id: str,
    metadata_patch: dict[str, Any],
) -> dict[str, Any] | None:
    store = _load()
    for row in store["chart_subjects"]:
        if str(row.get("id")) != chart_subject_id:
            continue
        current = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        row["metadata"] = {**current, **metadata_patch}
        _save(store)
        return row
    return None


def fetch_documents_registry(*, chart_subject_id: str | None = None) -> list[dict[str, Any]]:
    store = _load()
    pid = local_profile_id()
    rows = [r for r in store["documents"] if r.get("profile_id") == pid]
    if chart_subject_id:
        rows = [r for r in rows if str(r.get("chart_subject_id")) == chart_subject_id]
    return sorted(rows, key=lambda r: str(r.get("uploaded_at") or ""), reverse=True)


def insert_document_record(
    *,
    doc_id: str,
    filename: str,
    document_kind: DocumentKind,
    storage_bucket: str,
    storage_key: str,
    storage_url: str | None,
    chart_subject_id: str | None,
    extract_mode: str | None = None,
    episode_count: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    store = _load()
    row = {
        "id": str(uuid.uuid4()),
        "doc_id": doc_id,
        "profile_id": local_profile_id(),
        "chart_subject_id": chart_subject_id,
        "filename": filename,
        "document_kind": document_kind,
        "extract_mode": extract_mode,
        "episode_count": episode_count,
        "storage_bucket": storage_bucket,
        "storage_key": storage_key,
        "storage_url": storage_url,
        "metadata": metadata or {},
        "uploaded_at": _now(),
    }
    store["documents"].append(row)
    _save(store)
    return row


def upload_bytes_to_bucket(
    file_bytes: bytes,
    filename: str,
    *,
    content_type: str | None = None,
    bucket: str | None = None,
) -> dict[str, Any]:
    _FILES_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = Path(filename).name or "upload.bin"
    key = f"{uuid.uuid4().hex}/{safe_name}"
    dest = _FILES_DIR / key
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(file_bytes)
    b = bucket or "local-mock"
    return {
        "bucket": b,
        "key": key,
        "url": None,
        "size": len(file_bytes),
        "mimeType": content_type or "application/octet-stream",
        "uploadedAt": _now(),
        "local_path": str(dest),
    }


def list_chat_sessions_for_chart(*, chart_subject_id: str) -> list[dict[str, Any]]:
    store = _load()
    pid = local_profile_id()
    rows = [
        r
        for r in store["chat_sessions"]
        if r.get("profile_id") == pid and str(r.get("chart_subject_id")) == chart_subject_id
    ]
    return sorted(rows, key=lambda r: str(r.get("updated_at") or ""), reverse=True)


def upsert_chat_session_row(
    *,
    zep_thread_id: str,
    chart_subject_id: str | None,
    title: str | None = None,
) -> None:
    store = _load()
    pid = local_profile_id()
    now = _now()
    for row in store["chat_sessions"]:
        if row.get("zep_thread_id") == zep_thread_id:
            row["chart_subject_id"] = chart_subject_id
            row["title"] = title if title is not None else row.get("title")
            row["updated_at"] = now
            _save(store)
            return
    store["chat_sessions"].append(
        {
            "id": str(uuid.uuid4()),
            "profile_id": pid,
            "chart_subject_id": chart_subject_id,
            "zep_thread_id": zep_thread_id,
            "title": title,
            "created_at": now,
            "updated_at": now,
        }
    )
    _save(store)


def find_chat_session_by_thread(*, zep_thread_id: str) -> dict[str, Any] | None:
    store = _load()
    pid = local_profile_id()
    for row in store["chat_sessions"]:
        if row.get("profile_id") == pid and row.get("zep_thread_id") == zep_thread_id:
            return row
    return None


def touch_chat_session(*, zep_thread_id: str) -> None:
    store = _load()
    now = _now()
    changed = False
    for row in store["chat_sessions"]:
        if row.get("zep_thread_id") == zep_thread_id:
            row["updated_at"] = now
            changed = True
    if changed:
        _save(store)
