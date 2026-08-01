#!/usr/bin/env python
"""Export synthetic patient chart data into a portable SQLite file for sharing.

Prefer live Medplum (canonical FHIR store). Fall back to committed synthetic
fixtures when Medplum is unavailable or ``--source fixtures`` is set.

Binary/PDF payloads are omitted so the file stays small and shareable. Output is
gitignored under ``data/exports/`` by default (``*.sqlite``).

Examples::

    python scripts/export_patients_sqlite.py
    python scripts/export_patients_sqlite.py --source fixtures
    npm run medplum:export-sqlite
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from medtrace_agent.env import load_repo_env
from medtrace_agent.medplum import MedplumError, medplum_configured
from medtrace_agent.medplum_repository import CONSULTATION_SYSTEM, identifier_value, repository
from medtrace_agent.synthetic_fixtures import load_synthetic_store

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_OUT = _REPO_ROOT / "data" / "exports" / "patients.sqlite"

SCHEMA_SQL = """
CREATE TABLE meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE patients (
  id TEXT PRIMARY KEY,
  zep_user_id TEXT,
  name TEXT NOT NULL,
  age INTEGER,
  sex TEXT,
  dob TEXT,
  primary_doctor TEXT,
  last_visit TEXT,
  last_updated TEXT,
  risk TEXT,
  summary TEXT,
  document_count INTEGER DEFAULT 0,
  condition_count INTEGER DEFAULT 0,
  notes TEXT,
  tags_json TEXT
);

CREATE TABLE conditions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  patient_id TEXT NOT NULL,
  name TEXT NOT NULL,
  status TEXT,
  first_seen TEXT,
  last_mentioned TEXT,
  verification_status TEXT,
  source_document_id TEXT,
  FOREIGN KEY (patient_id) REFERENCES patients(id)
);

CREATE TABLE medications (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  patient_id TEXT NOT NULL,
  name TEXT NOT NULL,
  dose TEXT,
  frequency TEXT,
  status TEXT,
  start TEXT,
  end TEXT,
  verification_status TEXT,
  source_document_id TEXT,
  FOREIGN KEY (patient_id) REFERENCES patients(id)
);

CREATE TABLE allergies (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  patient_id TEXT NOT NULL,
  allergen TEXT NOT NULL,
  reaction TEXT,
  source TEXT,
  verification_status TEXT,
  source_document_id TEXT,
  FOREIGN KEY (patient_id) REFERENCES patients(id)
);

CREATE TABLE lab_trends (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  patient_id TEXT NOT NULL,
  test TEXT NOT NULL,
  latest TEXT,
  previous TEXT,
  status TEXT,
  trend TEXT,
  date TEXT,
  range TEXT,
  source TEXT,
  verification_status TEXT,
  source_document_id TEXT,
  FOREIGN KEY (patient_id) REFERENCES patients(id)
);

CREATE TABLE documents (
  doc_id TEXT PRIMARY KEY,
  patient_id TEXT NOT NULL,
  filename TEXT,
  document_kind TEXT,
  extract_mode TEXT,
  uploaded_at TEXT,
  status TEXT,
  review_status TEXT,
  episode_count INTEGER DEFAULT 0,
  FOREIGN KEY (patient_id) REFERENCES patients(id)
);

CREATE TABLE encounters (
  id TEXT PRIMARY KEY,
  patient_id TEXT NOT NULL,
  consultation_id TEXT,
  status TEXT,
  type TEXT,
  period_start TEXT,
  period_end TEXT,
  FOREIGN KEY (patient_id) REFERENCES patients(id)
);

CREATE TABLE timeline_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  patient_id TEXT NOT NULL,
  date TEXT NOT NULL,
  event TEXT NOT NULL,
  FOREIGN KEY (patient_id) REFERENCES patients(id)
);

CREATE INDEX idx_conditions_patient ON conditions(patient_id);
CREATE INDEX idx_medications_patient ON medications(patient_id);
CREATE INDEX idx_allergies_patient ON allergies(patient_id);
CREATE INDEX idx_lab_trends_patient ON lab_trends(patient_id);
CREATE INDEX idx_documents_patient ON documents(patient_id);
CREATE INDEX idx_encounters_patient ON encounters(patient_id);
CREATE INDEX idx_timeline_patient ON timeline_events(patient_id);
"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_or_none(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def _open_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_SQL)
    return conn


def _insert_patient(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO patients (
          id, zep_user_id, name, age, sex, dob, primary_doctor, last_visit,
          last_updated, risk, summary, document_count, condition_count, notes, tags_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            row["id"],
            row.get("zep_user_id"),
            row["name"],
            row.get("age"),
            row.get("sex"),
            row.get("dob"),
            row.get("primary_doctor"),
            row.get("last_visit"),
            row.get("last_updated"),
            row.get("risk"),
            row.get("summary"),
            row.get("document_count") or 0,
            row.get("condition_count") or 0,
            row.get("notes"),
            _json_or_none(row.get("tags")),
        ),
    )


def _insert_chart(
    conn: sqlite3.Connection,
    *,
    patient: dict[str, Any],
    conditions: list[dict[str, Any]],
    medications: list[dict[str, Any]],
    allergies: list[dict[str, Any]],
    labs: list[dict[str, Any]],
    documents: list[dict[str, Any]],
    encounters: list[dict[str, Any]],
    timeline: list[dict[str, Any]],
) -> None:
    patient_id = str(patient["id"])
    _insert_patient(conn, patient)

    for item in conditions:
        conn.execute(
            """
            INSERT INTO conditions (
              patient_id, name, status, first_seen, last_mentioned,
              verification_status, source_document_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                patient_id,
                item.get("name"),
                item.get("status"),
                item.get("first_seen"),
                item.get("last_mentioned"),
                item.get("verification_status"),
                item.get("source_document_id"),
            ),
        )

    for item in medications:
        conn.execute(
            """
            INSERT INTO medications (
              patient_id, name, dose, frequency, status, start, end,
              verification_status, source_document_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                patient_id,
                item.get("name"),
                item.get("dose"),
                item.get("frequency"),
                item.get("status"),
                item.get("start"),
                item.get("end"),
                item.get("verification_status"),
                item.get("source_document_id"),
            ),
        )

    for item in allergies:
        conn.execute(
            """
            INSERT INTO allergies (
              patient_id, allergen, reaction, source, verification_status, source_document_id
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                patient_id,
                item.get("allergen"),
                item.get("reaction"),
                item.get("source"),
                item.get("verification_status"),
                item.get("source_document_id"),
            ),
        )

    for item in labs:
        conn.execute(
            """
            INSERT INTO lab_trends (
              patient_id, test, latest, previous, status, trend, date, range,
              source, verification_status, source_document_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                patient_id,
                item.get("test"),
                item.get("latest"),
                item.get("previous"),
                item.get("status"),
                item.get("trend"),
                item.get("date"),
                item.get("range"),
                item.get("source"),
                item.get("verification_status"),
                item.get("source_document_id"),
            ),
        )

    for item in documents:
        conn.execute(
            """
            INSERT OR REPLACE INTO documents (
              doc_id, patient_id, filename, document_kind, extract_mode,
              uploaded_at, status, review_status, episode_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.get("doc_id"),
                patient_id,
                item.get("filename"),
                item.get("document_kind"),
                item.get("extract_mode"),
                item.get("uploaded_at"),
                item.get("status"),
                item.get("review_status"),
                item.get("episode_count") or 0,
            ),
        )

    for item in encounters:
        conn.execute(
            """
            INSERT OR REPLACE INTO encounters (
              id, patient_id, consultation_id, status, type, period_start, period_end
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.get("id"),
                patient_id,
                item.get("consultation_id"),
                item.get("status"),
                item.get("type"),
                item.get("period_start"),
                item.get("period_end"),
            ),
        )

    for group in timeline:
        date = str(group.get("date") or "")
        for event in group.get("events") or []:
            conn.execute(
                "INSERT INTO timeline_events (patient_id, date, event) VALUES (?, ?, ?)",
                (patient_id, date, str(event)),
            )


def _encounter_views(resources: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in resources.get("Encounter") or []:
        period = row.get("period") if isinstance(row.get("period"), dict) else {}
        encounter_type = ""
        types = row.get("type") or []
        if types and isinstance(types[0], dict):
            encounter_type = str(types[0].get("text") or "")
        out.append(
            {
                "id": str(row.get("id") or ""),
                "consultation_id": identifier_value(row, CONSULTATION_SYSTEM),
                "status": row.get("status"),
                "type": encounter_type or None,
                "period_start": period.get("start") if isinstance(period, dict) else None,
                "period_end": period.get("end") if isinstance(period, dict) else None,
            }
        )
    return out


def export_from_medplum(conn: sqlite3.Connection) -> int:
    repo = repository()
    patients = repo.list_patients()
    count = 0
    for fhir_patient in patients:
        patient_id = str(fhir_patient.get("id") or "")
        if not patient_id:
            continue
        resources = repo.clinical_resources(patient_id)
        documents = repo.list_documents(patient_id, resources)
        view = repo.patient_view(fhir_patient, document_count=len(documents), clinical=resources)
        tags = [
            str(tag.get("code"))
            for tag in (fhir_patient.get("meta") or {}).get("tag") or []
            if isinstance(tag, dict) and tag.get("code")
        ]
        patient_row = {
            **view,
            "condition_count": view.get("conditions") or 0,
            "notes": None,
            "tags": tags,
        }
        _insert_chart(
            conn,
            patient=patient_row,
            conditions=repo.condition_views(resources),
            medications=repo.medication_views(resources),
            allergies=repo.allergy_views(resources),
            labs=repo.lab_views(resources),
            documents=documents,
            encounters=_encounter_views(resources),
            timeline=repo.timeline_views(resources),
        )
        count += 1
    return count


def export_from_fixtures(conn: sqlite3.Connection, store_path: Path | None = None) -> int:
    store = load_synthetic_store(store_path)
    charts = store.get("chart_subjects") or []
    documents = store.get("documents") or []
    docs_by_chart: dict[str, list[dict[str, Any]]] = {}
    for doc in documents:
        chart_id = str(doc.get("chart_subject_id") or "")
        docs_by_chart.setdefault(chart_id, []).append(doc)

    count = 0
    for chart in charts:
        chart_id = str(chart.get("id") or "")
        fields = ((chart.get("metadata") or {}).get("fields") or {}) if isinstance(chart.get("metadata"), dict) else {}
        clinical = ((chart.get("metadata") or {}).get("clinical") or {}) if isinstance(chart.get("metadata"), dict) else {}
        chart_docs = docs_by_chart.get(chart_id, [])
        conditions = list(clinical.get("conditions") or [])
        medications = list(clinical.get("medications") or [])
        allergies = list(clinical.get("allergies") or [])
        labs = [
            {
                "test": item.get("test"),
                "latest": item.get("latest"),
                "previous": item.get("previous"),
                "status": item.get("status") or "Normal",
                "trend": "Stable",
                "date": item.get("date"),
                "range": item.get("range"),
                "source": None,
                "verification_status": "verified",
                "source_document_id": None,
            }
            for item in clinical.get("labs") or []
        ]
        document_rows = [
            {
                "doc_id": str(doc.get("doc_id") or doc.get("id") or ""),
                "filename": doc.get("filename"),
                "document_kind": doc.get("document_kind") or "clinical_pdf",
                "extract_mode": doc.get("extract_mode"),
                "uploaded_at": doc.get("uploaded_at"),
                "status": "Processed",
                "review_status": "Approved",
                "episode_count": doc.get("episode_count") or 0,
            }
            for doc in chart_docs
        ]
        high_lab = any(str(lab.get("status") or "").lower() == "high" for lab in labs)
        patient_row = {
            "id": chart_id,
            "zep_user_id": chart.get("zep_user_id"),
            "name": chart.get("display_name") or "Synthetic patient",
            "age": fields.get("age"),
            "sex": fields.get("sex") or "O",
            "dob": fields.get("dob"),
            "primary_doctor": fields.get("primary_doctor"),
            "last_visit": None,
            "last_updated": chart.get("created_at"),
            "risk": "High" if high_lab else "Medium" if conditions else "Low",
            "summary": None,
            "document_count": len(document_rows),
            "condition_count": len(conditions),
            "notes": fields.get("notes"),
            "tags": list(fields.get("tags") or []),
        }
        timeline: list[dict[str, Any]] = []
        if conditions or labs:
            events = [f"Condition recorded: {c.get('name')}" for c in conditions if c.get("name")]
            events.extend(f"{lab.get('test')}: {lab.get('latest')}" for lab in labs if lab.get("test"))
            if events:
                timeline.append({"date": str(chart.get("created_at") or "")[:10] or "Unknown", "events": events})

        _insert_chart(
            conn,
            patient=patient_row,
            conditions=conditions,
            medications=medications,
            allergies=allergies,
            labs=labs,
            documents=document_rows,
            encounters=[],
            timeline=timeline,
        )
        count += 1
    return count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        choices=("auto", "medplum", "fixtures"),
        default="auto",
        help="Data source (default: auto = Medplum if configured, else fixtures)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=_DEFAULT_OUT,
        help=f"Output SQLite path (default: {_DEFAULT_OUT})",
    )
    parser.add_argument(
        "--store",
        type=Path,
        default=None,
        help="Optional historical store.json for --source fixtures",
    )
    args = parser.parse_args(argv)

    load_repo_env()
    source = args.source
    if source == "auto":
        source = "medplum" if medplum_configured() else "fixtures"

    conn = _open_db(args.out)
    try:
        if source == "medplum":
            if not medplum_configured():
                print(
                    "Medplum is not configured. Set MEDPLUM_* in .env, or use --source fixtures.",
                    file=sys.stderr,
                )
                return 1
            try:
                count = export_from_medplum(conn)
            except MedplumError as exc:
                print(f"Medplum export failed: {exc}", file=sys.stderr)
                return 1
        else:
            count = export_from_fixtures(conn, args.store)

        conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?), (?, ?), (?, ?), (?, ?)",
            (
                "exported_at",
                _utc_now(),
                "source",
                source,
                "patient_count",
                str(count),
                "disclaimer",
                "Synthetic / demo clinical decision-support data only. Not real PHI.",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    size_kb = args.out.stat().st_size / 1024
    print(f"Wrote {count} patients from {source} → {args.out} ({size_kb:.1f} KiB)")
    print("Share this file. Recipients can open it with any SQLite client, e.g.:")
    print(f'  sqlite3 "{args.out}" ".tables"')
    print(f'  sqlite3 "{args.out}" "SELECT id, name, age, risk FROM patients;"')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
