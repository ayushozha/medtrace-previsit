#!/usr/bin/env python
"""Idempotently import synthetic fixtures or a historical JSON export into FHIR R4."""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any

from medtrace_agent.env import load_repo_env
from medtrace_agent.medplum import medplum_configured
from medtrace_agent.medplum_repository import FACT_SYSTEM, repository, utc_now
from medtrace_agent.synthetic_fixtures import load_synthetic_store


def _number(value: Any) -> tuple[float | None, str | None]:
    text = str(value or "")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None, None
    unit = text[match.end():].strip() or None
    return float(match.group()), unit


def _upsert_fact(resource: dict[str, Any], source_id: str) -> dict[str, Any]:
    resource["identifier"] = [{"system": FACT_SYSTEM, "value": source_id}]
    return repository().client.conditional_upsert(resource, identifier=f"{FACT_SYSTEM}|{source_id}")


def _seed_clinical(patient_id: str, chart_id: str, clinical: dict[str, Any]) -> int:
    count = 0
    for index, item in enumerate(clinical.get("conditions") or []):
        _upsert_fact({
            "resourceType": "Condition",
            "clinicalStatus": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-clinical", "code": "active" if str(item.get("status", "Active")).lower() == "active" else "inactive"}]},
            "verificationStatus": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-ver-status", "code": "confirmed"}]},
            "code": {"text": str(item.get("name") or "Unspecified condition")},
            "subject": {"reference": f"Patient/{patient_id}"},
            "recordedDate": utc_now(),
            "note": [{"text": "Verified synthetic fixture; not real clinical data."}],
        }, f"seed:{chart_id}:condition:{index}")
        count += 1
    for index, item in enumerate(clinical.get("medications") or []):
        status = "active" if str(item.get("status", "Active")).lower() == "active" else "completed"
        resource = {
            "resourceType": "MedicationStatement",
            "status": status,
            "medicationCodeableConcept": {"text": str(item.get("name") or "Unspecified medication")},
            "subject": {"reference": f"Patient/{patient_id}"},
            "dateAsserted": utc_now(),
            "note": [{"text": "Verified synthetic fixture; not real clinical data."}],
        }
        dosage = " ".join(str(v) for v in (item.get("dose"), item.get("frequency")) if v)
        if dosage:
            resource["dosage"] = [{"text": dosage}]
        _upsert_fact(resource, f"seed:{chart_id}:medication:{index}")
        count += 1
    for index, item in enumerate(clinical.get("allergies") or []):
        resource = {
            "resourceType": "AllergyIntolerance",
            "clinicalStatus": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical", "code": "active"}]},
            "verificationStatus": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/allergyintolerance-verification", "code": "confirmed"}]},
            "code": {"text": str(item.get("allergen") or "Unspecified allergen")},
            "patient": {"reference": f"Patient/{patient_id}"},
            "recordedDate": utc_now(),
            "note": [{"text": "Verified synthetic fixture; not real clinical data."}],
        }
        if item.get("reaction"):
            resource["reaction"] = [{"manifestation": [{"text": str(item["reaction"])}]}]
        _upsert_fact(resource, f"seed:{chart_id}:allergy:{index}")
        count += 1
    for index, item in enumerate(clinical.get("labs") or []):
        values = [
            ("latest", item.get("latest"), item.get("date"), "2026-05-01T00:00:00Z"),
            ("previous", item.get("previous"), None, "2026-01-01T00:00:00Z"),
        ]
        for label, raw, when, effective in values:
            if raw in {None, ""}:
                continue
            value, unit = _number(raw)
            resource = {
                "resourceType": "Observation",
                "status": "final",
                "code": {"text": str(item.get("test") or "Unspecified observation")},
                "subject": {"reference": f"Patient/{patient_id}"},
                "effectiveDateTime": effective,
                "interpretation": [{"text": str(item.get("status") or "Normal")}],
                "note": [{"text": f"Verified synthetic fixture ({when or label}); not real clinical data."}],
            }
            if value is not None:
                resource["valueQuantity"] = {"value": value, **({"unit": unit} if unit else {})}
            else:
                resource["valueString"] = str(raw)
            if item.get("range"):
                resource["referenceRange"] = [{"text": str(item["range"])}]
            _upsert_fact(resource, f"seed:{chart_id}:observation:{index}:{label}")
            count += 1
    return count


def main() -> None:
    load_repo_env()
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--store", type=Path)
    parser.add_argument("--include-zep", action="store_true", help="Reserved for importing existing Zep transcripts")
    args = parser.parse_args()
    store = load_synthetic_store(args.store)
    charts = store.get("chart_subjects") or []
    documents = store.get("documents") or []
    sessions = store.get("chat_sessions") or []
    if args.dry_run:
        print(f"Would import {len(charts)} patients, {len(documents)} documents, and {len(sessions)} thread headers.")
        return
    if not medplum_configured():
        raise SystemExit("Configure Medplum before seeding; run `npm run medplum:bootstrap` first.")
    if sessions and not args.include_zep:
        raise SystemExit("The source contains chat sessions. Re-run with --include-zep so transcript migration is explicit.")

    repo = repository()
    patient_ids: dict[str, str] = {}
    fact_count = 0
    for row in charts:
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        fields = metadata.get("fields") if isinstance(metadata.get("fields"), dict) else {}
        patient = repo.upsert_patient(
            zep_user_id=str(row.get("zep_user_id") or ""),
            display_name=str(row.get("display_name") or "Synthetic Patient"),
            dob=fields.get("dob"),
            age=fields.get("age"),
            sex=fields.get("sex"),
            primary_doctor=fields.get("primary_doctor"),
            tags=["synthetic", "historical-import"],
            legacy_chart_id=str(row.get("id") or ""),
        )
        chart_id = str(row.get("id") or "")
        patient_ids[chart_id] = str(patient["id"])
        clinical = metadata.get("clinical") if isinstance(metadata.get("clinical"), dict) else {}
        fact_count += _seed_clinical(str(patient["id"]), chart_id, clinical)

    for row in documents:
        chart_id = str(row.get("chart_subject_id") or "")
        patient_id = patient_ids.get(chart_id)
        if not patient_id:
            continue
        filename = str(row.get("filename") or "Synthetic placeholder.txt")
        file_path = Path("data/local_mock/files") / str(row.get("storage_key") or "")
        if file_path.is_file():
            data = file_path.read_bytes()
            content_type = str((row.get("metadata") or {}).get("content_type") or "application/octet-stream")
        else:
            data = (
                f"Synthetic placeholder for legacy registry item {filename}.\n"
                "The original fixture did not contain file bytes. This is not clinical data.\n"
            ).encode()
            content_type = "text/plain"
        doc, task = repo.create_document(
            patient_id=patient_id,
            data=data,
            filename=filename,
            content_type=content_type,
            document_kind=str(row.get("document_kind") or "clinical_pdf"),
            extract_mode=str(row.get("extract_mode") or "local_fixture"),
            source_doc_id=str(row.get("doc_id") or ""),
        )
        repo.finish_task(task, episode_count=int(row.get("episode_count") or 0))

    for row in sessions:
        patient_id = patient_ids.get(str(row.get("chart_subject_id") or ""))
        if not patient_id:
            continue
        thread = repo.create_thread(
            patient_id=patient_id,
            zep_thread_id=str(row.get("zep_thread_id") or ""),
            title=row.get("title"),
        )
        if args.include_zep:
            try:
                from medtrace_agent.zep.memory import get_zep_client

                source = get_zep_client().thread.get(
                    thread_id=str(row.get("zep_thread_id") or ""),
                    lastn=200,
                )
            except Exception as exc:
                raise SystemExit(
                    f"Could not read source Zep transcript {row.get('zep_thread_id')}; "
                    "no transcript migration was claimed."
                ) from exc
            messages = sorted(list(source.messages or []), key=lambda message: str(message.created_at or ""))
            for index, message in enumerate(messages):
                role = "user" if message.role == "user" else "assistant"
                source_uuid = getattr(message, "uuid_", None) or getattr(message, "uuid", None) or index
                repo.create_message(
                    thread=thread,
                    patient_id=patient_id,
                    role=role,
                    content=str(message.content or ""),
                    request_id=f"legacy-zep-{source_uuid}",
                )

    patient_total = len(repo.list_patients())
    document_total = len(repo.client.search("DocumentReference", {"_count": 500}))
    thread_total = len(repo.client.search("Communication", {
        "category": "https://medtrace.local/fhir/code|ai-chat",
        "part-of:missing": "true",
        "_count": 500,
    }))
    print(f"Imported/reconciled {len(charts)} patients, {fact_count} facts, and {len(documents)} documents.")
    print(
        "Reconciliation counts — "
        f"source/Medplum: patients {len(charts)}/{patient_total}, "
        f"documents {len(documents)}/{document_total}, threads {len(sessions)}/{thread_total}."
    )


if __name__ == "__main__":
    main()
