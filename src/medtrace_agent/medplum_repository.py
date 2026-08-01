"""FHIR R4 repository and MedTrace-domain mappings for Medplum."""

from __future__ import annotations

import os
import re
import uuid
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import urlencode

from medtrace_agent.medplum import MedplumClient, get_medplum_client
from medtrace_agent.medplum_extraction import ExtractedClinicalFacts


IDENTIFIER_BASE = os.environ.get("MEDTRACE_IDENTIFIER_BASE", "https://medtrace.local/fhir/identifier").rstrip("/")
CODE_SYSTEM = "https://medtrace.local/fhir/code"
TAG_SYSTEM = "https://medtrace.local/fhir/tag"
ZEP_USER_SYSTEM = f"{IDENTIFIER_BASE}/zep-user"
LEGACY_CHART_SYSTEM = f"{IDENTIFIER_BASE}/chart-subject"
DOCUMENT_SYSTEM = f"{IDENTIFIER_BASE}/document"
THREAD_SYSTEM = f"{IDENTIFIER_BASE}/zep-thread"
MESSAGE_REQUEST_SYSTEM = f"{IDENTIFIER_BASE}/message-request"
FACT_SYSTEM = f"{IDENTIFIER_BASE}/extracted-fact"
TASK_SYSTEM = f"{IDENTIFIER_BASE}/projection-task"
AI_UNVERIFIED_TAG = {"system": TAG_SYSTEM, "code": "ai-extracted-unverified", "display": "AI extracted — unverified"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _identifiers(resource: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in resource.get("identifier") or [] if isinstance(item, dict)]


def identifier_value(resource: dict[str, Any], system: str) -> str | None:
    for item in _identifiers(resource):
        if item.get("system") == system and item.get("value"):
            return str(item["value"])
    return None


def _coding_text(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    if value.get("text"):
        return str(value["text"])
    for coding in value.get("coding") or []:
        if isinstance(coding, dict) and (coding.get("display") or coding.get("code")):
            return str(coding.get("display") or coding.get("code"))
    return ""


def _reference_id(reference: Any, resource_type: str) -> str | None:
    if not isinstance(reference, dict):
        return None
    raw = str(reference.get("reference") or "")
    prefix = f"{resource_type}/"
    return raw[len(prefix):] if raw.startswith(prefix) else None


def _tag(resource: dict[str, Any], code: str, system: str = TAG_SYSTEM) -> bool:
    return any(
        isinstance(tag, dict) and tag.get("system") == system and tag.get("code") == code
        for tag in (resource.get("meta") or {}).get("tag") or []
    )


def _tag_value(resource: dict[str, Any], system: str) -> str | None:
    for tag in (resource.get("meta") or {}).get("tag") or []:
        if isinstance(tag, dict) and tag.get("system") == system and tag.get("code"):
            return str(tag["code"])
    return None


def _display_name(patient: dict[str, Any]) -> str:
    names = patient.get("name") or []
    if not names:
        return "Unnamed patient"
    name = names[0] if isinstance(names[0], dict) else {}
    if name.get("text"):
        return str(name["text"])
    parts = [*(str(v) for v in name.get("given") or []), str(name.get("family") or "")]
    return " ".join(v for v in parts if v).strip() or "Unnamed patient"


def _age(birth_date: str | None) -> int:
    if not birth_date:
        return 0
    try:
        born = date.fromisoformat(birth_date)
    except ValueError:
        return 0
    today = date.today()
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


def _legacy_sex(gender: str | None) -> str:
    return {"male": "M", "female": "F"}.get(str(gender or "").lower(), "O")


def _fhir_gender(sex: str | None) -> str:
    return {"M": "male", "F": "female", "O": "other"}.get(str(sex or "").upper(), "unknown")


def _resource_date(resource: dict[str, Any]) -> str | None:
    for field in ("effectiveDateTime", "issued", "recordedDate", "onsetDateTime", "authoredOn", "date", "sent"):
        if resource.get(field):
            return str(resource[field])
    period = resource.get("effectivePeriod") or resource.get("period") or resource.get("onsetPeriod")
    if isinstance(period, dict) and period.get("start"):
        return str(period["start"])
    return str((resource.get("meta") or {}).get("lastUpdated") or "") or None


def _verification(resource: dict[str, Any]) -> str:
    return "unverified" if _tag(resource, "ai-extracted-unverified") else "verified"


class MedplumRepository:
    def __init__(self, client: MedplumClient | None = None) -> None:
        self.client = client or get_medplum_client()

    # ---- patients ---------------------------------------------------------

    def get_patient(self, patient_id: str) -> dict[str, Any] | None:
        try:
            resource = self.client.read("Patient", patient_id)
        except Exception as exc:
            from medtrace_agent.medplum import MedplumError

            if isinstance(exc, MedplumError) and exc.status_code == 404:
                return None
            raise
        return resource

    def find_patient_by_zep(self, zep_user_id: str) -> dict[str, Any] | None:
        return self.client.search_one("Patient", {"identifier": f"{ZEP_USER_SYSTEM}|{zep_user_id}"})

    def list_patients(self) -> list[dict[str, Any]]:
        return self.client.search("Patient", {"_count": 200, "_sort": "-_lastUpdated"})

    def _ensure_practitioner_role(self, display_name: str) -> dict[str, Any]:
        slug = re.sub(r"[^a-z0-9]+", "-", display_name.lower()).strip("-") or uuid.uuid4().hex
        identifier = f"{IDENTIFIER_BASE}/practitioner|{slug}"
        practitioner = self.client.conditional_upsert(
            {
                "resourceType": "Practitioner",
                "active": True,
                "identifier": [{"system": f"{IDENTIFIER_BASE}/practitioner", "value": slug}],
                "name": [{"text": display_name}],
            },
            identifier=identifier,
        )
        role_identifier = f"{IDENTIFIER_BASE}/practitioner-role|{slug}"
        return self.client.conditional_upsert(
            {
                "resourceType": "PractitionerRole",
                "active": True,
                "identifier": [{"system": f"{IDENTIFIER_BASE}/practitioner-role", "value": slug}],
                "practitioner": {
                    "reference": f"Practitioner/{practitioner['id']}",
                    "display": display_name,
                },
                "code": [{"text": "Primary clinician"}],
            },
            identifier=role_identifier,
        )

    def upsert_patient(
        self,
        *,
        zep_user_id: str,
        display_name: str,
        dob: str | None = None,
        age: int | None = None,
        sex: str | None = None,
        primary_doctor: str | None = None,
        tags: list[str] | None = None,
        legacy_chart_id: str | None = None,
    ) -> dict[str, Any]:
        birth_date = dob
        meta_tags = [{"system": TAG_SYSTEM, "code": tag} for tag in (tags or [])]
        if not birth_date and age is not None:
            birth_date = f"{max(1900, date.today().year - int(age)):04d}-01-01"
            meta_tags.append({"system": TAG_SYSTEM, "code": "estimated-birthdate"})
        identifiers = [{"system": ZEP_USER_SYSTEM, "value": zep_user_id}]
        if legacy_chart_id:
            identifiers.append({"system": LEGACY_CHART_SYSTEM, "value": legacy_chart_id})
        resource: dict[str, Any] = {
            "resourceType": "Patient",
            "active": True,
            "identifier": identifiers,
            "name": [{"text": display_name}],
            "gender": _fhir_gender(sex),
        }
        if birth_date:
            resource["birthDate"] = birth_date
        if meta_tags:
            resource["meta"] = {"tag": meta_tags}
        if primary_doctor:
            practitioner_role = self._ensure_practitioner_role(primary_doctor)
            resource["generalPractitioner"] = [
                {"reference": f"PractitionerRole/{practitioner_role['id']}", "display": primary_doctor}
            ]
        return self.client.conditional_upsert(resource, identifier=f"{ZEP_USER_SYSTEM}|{zep_user_id}")

    def patient_view(self, patient: dict[str, Any], *, document_count: int = 0, clinical: dict[str, list[dict[str, Any]]] | None = None) -> dict[str, Any]:
        general = patient.get("generalPractitioner") or []
        doctor = general[0].get("display") if general and isinstance(general[0], dict) else None
        conditions = (clinical or {}).get("Condition") or []
        observations = (clinical or {}).get("Observation") or []
        high = any(self._observation_status(row) in {"High", "Low"} for row in observations)
        last_dates = [value for value in (_resource_date(r) for rows in (clinical or {}).values() for r in rows) if value]
        last_visit = max(last_dates)[:10] if last_dates else None
        return {
            "id": str(patient.get("id") or ""),
            "zep_user_id": identifier_value(patient, ZEP_USER_SYSTEM) or "",
            "name": _display_name(patient),
            "age": _age(patient.get("birthDate")),
            "sex": _legacy_sex(patient.get("gender")),
            "dob": patient.get("birthDate"),
            "primary_doctor": doctor,
            "last_visit": last_visit,
            "last_updated": (patient.get("meta") or {}).get("lastUpdated"),
            "document_count": document_count,
            "conditions": len(conditions),
            "risk": "High" if high else "Medium" if conditions else "Low",
            "summary": None,
            "metadata": {"fhir_resource_type": "Patient"},
        }

    # ---- clinical reads and mappings ------------------------------------

    def clinical_resources(self, patient_id: str) -> dict[str, list[dict[str, Any]]]:
        searches = {
            "Condition": {"patient": patient_id, "_count": 200},
            "MedicationStatement": {"subject": f"Patient/{patient_id}", "_count": 200},
            "AllergyIntolerance": {"patient": patient_id, "_count": 200},
            "Observation": {"subject": f"Patient/{patient_id}", "_count": 200},
            "Encounter": {"subject": f"Patient/{patient_id}", "_count": 200},
            "DocumentReference": {"subject": f"Patient/{patient_id}", "_count": 200},
            "Provenance": {"target": f"Patient/{patient_id}", "_count": 200},
            "Task": {"patient": patient_id, "_count": 200},
        }
        entries = [
            {"request": {"method": "GET", "url": f"{resource_type}?{urlencode(params)}"}}
            for resource_type, params in searches.items()
        ]
        response = self.client.batch(entries)
        result: dict[str, list[dict[str, Any]]] = {key: [] for key in searches}
        for resource_type, entry in zip(searches, response.get("entry") or [], strict=False):
            bundle = entry.get("resource") if isinstance(entry, dict) else None
            if not isinstance(bundle, dict):
                continue
            result[resource_type] = [
                child["resource"]
                for child in bundle.get("entry") or []
                if isinstance(child, dict) and isinstance(child.get("resource"), dict)
            ]
        return result

    @staticmethod
    def _provenance_sources(resources: dict[str, list[dict[str, Any]]]) -> dict[str, str]:
        out: dict[str, str] = {}
        for provenance in resources.get("Provenance") or []:
            source_doc = next(
                (_reference_id(target, "DocumentReference") for target in provenance.get("target") or [] if _reference_id(target, "DocumentReference")),
                None,
            )
            if not source_doc:
                continue
            for target in provenance.get("target") or []:
                reference = str(target.get("reference") or "") if isinstance(target, dict) else ""
                if reference and not reference.startswith(("Patient/", "DocumentReference/")):
                    out[reference] = source_doc
        return out

    def condition_views(self, resources: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
        sources = self._provenance_sources(resources)
        out = []
        for row in resources.get("Condition") or []:
            status = _coding_text(row.get("clinicalStatus")) or "Active"
            ref = f"Condition/{row.get('id')}"
            out.append({
                "name": _coding_text(row.get("code")) or "Unspecified condition",
                "status": status.title(),
                "first_seen": _resource_date(row),
                "last_mentioned": (row.get("meta") or {}).get("lastUpdated"),
                "verification_status": _verification(row),
                "source_document_id": sources.get(ref),
            })
        return out

    def medication_views(self, resources: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
        sources = self._provenance_sources(resources)
        out = []
        for row in resources.get("MedicationStatement") or []:
            dosage = (row.get("dosage") or [{}])[0]
            text = str(dosage.get("text") or "") if isinstance(dosage, dict) else ""
            dose = next(iter(re.findall(r"\b\d+(?:\.\d+)?\s*(?:mg|mcg|g|mL|units?)\b", text, re.I)), None)
            frequency = text.replace(dose, "").strip(" ,-;") if dose else text or None
            ref = f"MedicationStatement/{row.get('id')}"
            status = str(row.get("status") or "active")
            out.append({
                "name": _coding_text(row.get("medicationCodeableConcept")) or "Unspecified medication",
                "dose": dose,
                "frequency": frequency,
                "status": "Previous" if status in {"completed", "stopped", "entered-in-error"} else "Active",
                "start": _resource_date(row),
                "end": (row.get("effectivePeriod") or {}).get("end") if isinstance(row.get("effectivePeriod"), dict) else None,
                "verification_status": _verification(row),
                "source_document_id": sources.get(ref),
            })
        return out

    def allergy_views(self, resources: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
        sources = self._provenance_sources(resources)
        out = []
        for row in resources.get("AllergyIntolerance") or []:
            reaction = ""
            reactions = row.get("reaction") or []
            if reactions and isinstance(reactions[0], dict):
                manifestations = reactions[0].get("manifestation") or []
                if manifestations:
                    reaction = _coding_text(manifestations[0])
            ref = f"AllergyIntolerance/{row.get('id')}"
            out.append({
                "allergen": _coding_text(row.get("code")) or "Unspecified allergen",
                "reaction": reaction or None,
                "source": f"Document {sources[ref]}" if sources.get(ref) else None,
                "verification_status": _verification(row),
                "source_document_id": sources.get(ref),
            })
        return out

    @staticmethod
    def _observation_value(row: dict[str, Any]) -> tuple[str, float | None]:
        quantity = row.get("valueQuantity")
        if isinstance(quantity, dict) and quantity.get("value") is not None:
            value = float(quantity["value"])
            unit = str(quantity.get("unit") or quantity.get("code") or "")
            return f"{value:g}{(' ' + unit) if unit else ''}", value
        text = row.get("valueString") or _coding_text(row.get("valueCodeableConcept"))
        if text is None:
            return "", None
        match = re.search(r"-?\d+(?:\.\d+)?", str(text))
        return str(text), float(match.group()) if match else None

    def _observation_status(self, row: dict[str, Any]) -> str:
        interpretations = row.get("interpretation") or []
        text = " ".join(_coding_text(item).lower() for item in interpretations).strip()
        if text in {"h", "high", "hh"} or any(word in text for word in ("elevated", "abnormal")):
            return "High"
        if text in {"l", "low", "ll"}:
            return "Low"
        return "Normal"

    def lab_views(self, resources: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
        sources = self._provenance_sources(resources)
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in resources.get("Observation") or []:
            name = _coding_text(row.get("code"))
            if name:
                grouped[name].append(row)
        out = []
        for name, rows in grouped.items():
            rows.sort(key=lambda row: _resource_date(row) or "", reverse=True)
            latest = rows[0]
            latest_text, latest_num = self._observation_value(latest)
            previous_text, previous_num = self._observation_value(rows[1]) if len(rows) > 1 else (None, None)
            trend = "Stable"
            if latest_num is not None and previous_num is not None:
                if latest_num > previous_num:
                    trend = "Worsening"
                elif latest_num < previous_num:
                    trend = "Improving"
            ranges = latest.get("referenceRange") or []
            range_text = ranges[0].get("text") if ranges and isinstance(ranges[0], dict) else None
            ref = f"Observation/{latest.get('id')}"
            out.append({
                "test": name,
                "latest": latest_text,
                "previous": previous_text,
                "status": self._observation_status(latest),
                "trend": trend,
                "date": _resource_date(latest),
                "range": range_text,
                "source": f"Document {sources[ref]}" if sources.get(ref) else None,
                "verification_status": _verification(latest),
                "source_document_id": sources.get(ref),
            })
        out.sort(key=lambda item: 0 if item["status"] != "Normal" else 1)
        return out

    def abnormal_views(self, resources: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
        return [
            {
                "test": lab["test"],
                "value": lab["latest"],
                "status": lab["status"].lower(),
                "source": lab.get("source"),
                "verification_status": lab.get("verification_status"),
                "source_document_id": lab.get("source_document_id"),
            }
            for lab in self.lab_views(resources)
            if lab["status"] != "Normal"
        ]

    def alert_views(self, resources: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
        alerts = [
            {"message": f"Documented allergy: {item['allergen']}", "priority": "High", "type": "allergy", "evidence": item.get("source")}
            for item in self.allergy_views(resources)
        ]
        alerts.extend(
            {"message": f"{item['test']} is {item['status'].lower()} ({item['latest']})", "priority": "High" if item["status"] == "High" else "Medium", "type": "value_trend", "evidence": item.get("source")}
            for item in self.lab_views(resources)
            if item["status"] != "Normal"
        )
        return alerts[:8]

    def timeline_views(self, resources: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
        grouped: dict[str, list[str]] = defaultdict(list)
        labels = {
            "Condition": lambda r: f"Condition recorded: {_coding_text(r.get('code'))}",
            "MedicationStatement": lambda r: f"Medication: {_coding_text(r.get('medicationCodeableConcept'))}",
            "Observation": lambda r: f"{_coding_text(r.get('code'))}: {self._observation_value(r)[0]}",
            "Encounter": lambda r: f"Encounter: {_coding_text(r.get('type', [{}])[0]) or r.get('status', 'recorded')}",
            "DocumentReference": lambda r: f"Document: {((r.get('content') or [{}])[0].get('attachment') or {}).get('title', 'uploaded')}",
        }
        for resource_type, formatter in labels.items():
            for row in resources.get(resource_type) or []:
                when = (_resource_date(row) or "Unknown")[:10]
                event = formatter(row)
                if event and event not in grouped[when]:
                    grouped[when].append(event)
        return [{"date": key, "events": grouped[key][:6]} for key in sorted(grouped)]

    def insight_views(self, resources: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
        return [
            {"title": alert["type"].replace("_", " ").title(), "detail": alert["message"], "evidence": [alert.get("evidence") or "FHIR record"], "priority": alert["priority"]}
            for alert in self.alert_views(resources)[:3]
        ]

    # ---- documents -------------------------------------------------------

    def _document_task(self, doc_id: str, tasks: list[dict[str, Any]]) -> dict[str, Any] | None:
        matching = [task for task in tasks if _reference_id(task.get("focus"), "DocumentReference") == doc_id and _coding_text(task.get("code")) == "document-processing"]
        matching.sort(key=lambda task: str(task.get("lastModified") or task.get("authoredOn") or ""), reverse=True)
        return matching[0] if matching else None

    def document_view(self, doc: dict[str, Any], tasks: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        content = doc.get("content") or []
        attachment = content[0].get("attachment") if content and isinstance(content[0], dict) else {}
        attachment = attachment if isinstance(attachment, dict) else {}
        task = self._document_task(str(doc.get("id") or ""), tasks or [])
        task_status = str((task or {}).get("status") or "completed")
        status = "Failed" if task_status in {"failed", "entered-in-error"} else "Processing" if task_status in {"requested", "received", "accepted", "ready", "in-progress"} else "Processed"
        episode_count = 0
        for output in (task or {}).get("output") or []:
            if isinstance(output, dict) and _coding_text(output.get("type")) == "zep-episode-count":
                episode_count = int(output.get("valueInteger") or 0)
        binary_ref = str(attachment.get("url") or "")
        return {
            "doc_id": str(doc.get("id") or ""),
            "filename": str(attachment.get("title") or "file"),
            "document_kind": _coding_text(doc.get("type")) or "clinical_pdf",
            "extract_mode": _tag_value(doc, f"{TAG_SYSTEM}/extract-mode"),
            "episode_count": episode_count,
            "storage_url": attachment.get("url"),
            "storage_key": binary_ref if binary_ref.startswith("Binary/") else None,
            "storage_bucket": "medplum",
            "uploaded_at": str(doc.get("date") or (doc.get("meta") or {}).get("lastUpdated") or ""),
            "status": status,
            "review_status": "Needs review" if _tag(doc, "ai-extracted-unverified") else "Approved",
            "processing_error": _coding_text((task or {}).get("statusReason")) or None,
        }

    def list_documents(self, patient_id: str, resources: dict[str, list[dict[str, Any]]] | None = None) -> list[dict[str, Any]]:
        data = resources or self.clinical_resources(patient_id)
        docs = [self.document_view(doc, data.get("Task")) for doc in data.get("DocumentReference") or []]
        docs.sort(key=lambda item: item["uploaded_at"], reverse=True)
        return docs

    def create_document(
        self,
        *,
        patient_id: str,
        data: bytes,
        filename: str,
        content_type: str,
        document_kind: str,
        extract_mode: str,
        source_doc_id: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if source_doc_id:
            existing = self.client.search_one(
                "DocumentReference",
                {"identifier": f"{DOCUMENT_SYSTEM}|{source_doc_id}"},
            )
            if existing:
                task = self.client.search_one(
                    "Task",
                    {"focus": f"DocumentReference/{existing['id']}"},
                )
                if task:
                    return existing, task
        binary = self.client.create_binary(data, content_type=content_type)
        doc_identifier = source_doc_id or uuid.uuid4().hex
        doc = self.client.create({
            "resourceType": "DocumentReference",
            "status": "current",
            "identifier": [{"system": DOCUMENT_SYSTEM, "value": doc_identifier}],
            "meta": {"tag": [
                {"system": f"{TAG_SYSTEM}/extract-mode", "code": extract_mode},
                AI_UNVERIFIED_TAG,
            ]},
            "type": {"text": document_kind},
            "subject": {"reference": f"Patient/{patient_id}"},
            "date": utc_now(),
            "content": [{"attachment": {"contentType": content_type, "url": f"Binary/{binary['id']}", "title": filename, "size": len(data)}}],
        })
        task = self.client.create({
            "resourceType": "Task",
            "status": "in-progress",
            "intent": "order",
            "code": {"coding": [{"system": CODE_SYSTEM, "code": "document-processing", "display": "document-processing"}], "text": "document-processing"},
            "focus": {"reference": f"DocumentReference/{doc['id']}"},
            "for": {"reference": f"Patient/{patient_id}"},
            "authoredOn": utc_now(),
            "lastModified": utc_now(),
        })
        return doc, task

    def attach_extracted_text(self, doc: dict[str, Any], task: dict[str, Any], text: str) -> tuple[dict[str, Any], dict[str, Any]]:
        binary = self.client.create_binary(text.encode("utf-8"), content_type="text/plain; charset=utf-8")
        updated_doc = {**doc, "content": [*(doc.get("content") or []), {"attachment": {"contentType": "text/plain", "url": f"Binary/{binary['id']}", "title": "AI extracted text"}}]}
        doc = self.client.update(updated_doc)
        task = self.client.update({**task, "input": [*(task.get("input") or []), {"type": {"text": "extracted-text"}, "valueReference": {"reference": f"Binary/{binary['id']}"}}], "lastModified": utc_now()})
        return doc, task

    def finish_task(self, task: dict[str, Any], *, episode_count: int = 0, error: str | None = None) -> dict[str, Any]:
        updated = {**task, "status": "failed" if error else "completed", "lastModified": utc_now()}
        if error:
            updated["statusReason"] = {"text": error[:500]}
        else:
            updated.pop("statusReason", None)
            updated["output"] = [{"type": {"text": "zep-episode-count"}, "valueInteger": episode_count}]
        return self.client.update(updated)

    def create_extracted_facts(self, *, patient_id: str, doc_id: str, facts: ExtractedClinicalFacts, model_name: str) -> list[str]:
        entries: list[dict[str, Any]] = []
        target_refs: list[dict[str, Any]] = [
            {"reference": f"Patient/{patient_id}"},
            {"reference": f"DocumentReference/{doc_id}"},
        ]

        def add(resource: dict[str, Any], kind: str, index: int) -> None:
            value = f"{doc_id}:{kind}:{index}"
            full_url = f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, value)}"
            resource["identifier"] = [{"system": FACT_SYSTEM, "value": value}]
            resource["meta"] = {"tag": [AI_UNVERIFIED_TAG]}
            entries.append({"fullUrl": full_url, "resource": resource, "request": {"method": "PUT", "url": f"{resource['resourceType']}?identifier={FACT_SYSTEM}|{value}"}})
            target_refs.append({"reference": full_url})

        for index, item in enumerate(facts.conditions):
            add({
                "resourceType": "Condition",
                "clinicalStatus": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-clinical", "code": "active"}]},
                "verificationStatus": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-ver-status", "code": "unconfirmed"}]},
                "code": {"text": item.name},
                "subject": {"reference": f"Patient/{patient_id}"},
                "recordedDate": utc_now(),
                "evidence": [{"detail": [{"reference": f"DocumentReference/{doc_id}"}]}],
                "note": [{"text": f"AI extracted from page {item.source_page or 'unknown'}; requires human verification."}],
            }, "condition", index)
        for index, item in enumerate(facts.medications):
            resource = {
                "resourceType": "MedicationStatement",
                "status": "active",
                "medicationCodeableConcept": {"text": item.name},
                "subject": {"reference": f"Patient/{patient_id}"},
                "dateAsserted": utc_now(),
                "derivedFrom": [{"reference": f"DocumentReference/{doc_id}"}],
                "note": [{"text": f"AI extracted from page {item.source_page or 'unknown'}; requires human verification."}],
            }
            if item.dosage_text:
                resource["dosage"] = [{"text": item.dosage_text}]
            add(resource, "medication", index)
        for index, item in enumerate(facts.allergies):
            resource = {
                "resourceType": "AllergyIntolerance",
                "clinicalStatus": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical", "code": "active"}]},
                "verificationStatus": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/allergyintolerance-verification", "code": "unconfirmed"}]},
                "code": {"text": item.substance},
                "patient": {"reference": f"Patient/{patient_id}"},
                "recordedDate": utc_now(),
                "note": [{"text": f"AI extracted from page {item.source_page or 'unknown'}; requires human verification."}],
            }
            if item.reaction:
                resource["reaction"] = [{"manifestation": [{"text": item.reaction}]}]
            add(resource, "allergy", index)
        for index, item in enumerate(facts.observations):
            resource = {
                "resourceType": "Observation",
                "status": "preliminary",
                "code": {"text": item.name},
                "subject": {"reference": f"Patient/{patient_id}"},
                "effectiveDateTime": utc_now(),
                "derivedFrom": [{"reference": f"DocumentReference/{doc_id}"}],
                "note": [{"text": f"AI extracted from page {item.source_page or 'unknown'}; requires human verification."}],
            }
            if item.value is not None:
                try:
                    resource["valueQuantity"] = {"value": float(item.value.replace(",", "")), **({"unit": item.unit} if item.unit else {})}
                except ValueError:
                    resource["valueString"] = item.value
            if item.reference_range:
                resource["referenceRange"] = [{"text": item.reference_range}]
            if item.flag:
                resource["interpretation"] = [{"text": item.flag}]
            add(resource, "observation", index)
        if not entries:
            return []
        provenance = {
            "resourceType": "Provenance",
            "meta": {"tag": [{"system": f"{TAG_SYSTEM}/provenance", "code": doc_id}]},
            "target": target_refs,
            "recorded": utc_now(),
            "activity": {"coding": [{"system": CODE_SYSTEM, "code": "ai-document-extraction"}], "text": "AI document extraction"},
            "agent": [{"type": {"text": "AI extraction model"}, "who": {"display": model_name}}],
            "entity": [{"role": "source", "what": {"reference": f"DocumentReference/{doc_id}"}}],
        }
        entries.append({
            "resource": provenance,
            "request": {"method": "PUT", "url": f"Provenance?_tag={TAG_SYSTEM}/provenance|{doc_id}"},
        })
        response = self.client.transaction(entries)
        created: list[str] = []
        for entry in response.get("entry") or []:
            location = str((entry.get("response") or {}).get("location") or "") if isinstance(entry, dict) else ""
            if location:
                created.append(location.split("/_history", 1)[0])
        return created

    # ---- messaging -------------------------------------------------------

    def create_thread(self, *, patient_id: str, zep_thread_id: str, title: str | None) -> dict[str, Any]:
        return self.client.conditional_upsert({
            "resourceType": "Communication",
            "status": "in-progress",
            "identifier": [{"system": THREAD_SYSTEM, "value": zep_thread_id}],
            "category": [{"coding": [{"system": CODE_SYSTEM, "code": "ai-chat"}], "text": "ai-chat"}],
            "subject": {"reference": f"Patient/{patient_id}"},
            "topic": {"text": title or "Clinical assistant conversation"},
            "recipient": [{"reference": f"Patient/{patient_id}"}],
            "sent": utc_now(),
        }, identifier=f"{THREAD_SYSTEM}|{zep_thread_id}")

    def list_threads(self, patient_id: str) -> list[dict[str, Any]]:
        rows = self.client.search("Communication", {
            "subject": f"Patient/{patient_id}",
            "category": f"{CODE_SYSTEM}|ai-chat",
            "part-of:missing": "true",
            "_sort": "-sent",
            "_count": 100,
        })
        return rows

    def thread_by_zep(self, zep_thread_id: str) -> dict[str, Any] | None:
        return self.client.search_one("Communication", {"identifier": f"{THREAD_SYSTEM}|{zep_thread_id}"})

    def thread_view(self, thread: dict[str, Any]) -> dict[str, Any]:
        topic = thread.get("topic") or {}
        created = str(thread.get("sent") or (thread.get("meta") or {}).get("lastUpdated") or "")
        return {
            "id": str(thread.get("id") or ""),
            "zep_thread_id": identifier_value(thread, THREAD_SYSTEM) or "",
            "title": topic.get("text") if isinstance(topic, dict) else None,
            "created_at": created,
            "updated_at": str(thread.get("received") or (thread.get("meta") or {}).get("lastUpdated") or created),
        }

    def list_messages(self, thread_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.client.search("Communication", {"part-of": f"Communication/{thread_id}", "_sort": "sent", "_count": min(max(limit, 1), 200)})
        return rows[-limit:]

    def create_message(self, *, thread: dict[str, Any], patient_id: str, role: str, content: str, request_id: str) -> dict[str, Any]:
        identifier = f"{request_id}:{role}"
        sent = utc_now()
        resource = {
            "resourceType": "Communication",
            "status": "completed",
            "identifier": [{"system": MESSAGE_REQUEST_SYSTEM, "value": identifier}],
            "meta": {"tag": [{"system": f"{TAG_SYSTEM}/message-role", "code": role}]},
            "category": [{"coding": [{"system": CODE_SYSTEM, "code": "ai-chat"}], "text": "ai-chat"}],
            "subject": {"reference": f"Patient/{patient_id}"},
            "partOf": [{"reference": f"Communication/{thread['id']}"}],
            "sender": {"reference": f"Patient/{patient_id}"} if role == "user" else {"display": "MedTrace Assistant"},
            "recipient": [{"display": "MedTrace Assistant"}] if role == "user" else [{"reference": f"Patient/{patient_id}"}],
            "sent": sent,
            "payload": [{"contentString": content}],
        }
        message = self.client.conditional_upsert(resource, identifier=f"{MESSAGE_REQUEST_SYSTEM}|{identifier}")
        self.client.update({**thread, "received": sent})
        return message

    def message_by_request(self, request_id: str, role: str) -> dict[str, Any] | None:
        return self.client.search_one(
            "Communication",
            {"identifier": f"{MESSAGE_REQUEST_SYSTEM}|{request_id}:{role}"},
        )

    def message_view(self, message: dict[str, Any]) -> dict[str, Any]:
        payload = message.get("payload") or []
        content = payload[0].get("contentString") if payload and isinstance(payload[0], dict) else ""
        role = _tag_value(message, f"{TAG_SYSTEM}/message-role") or "assistant"
        return {
            "id": str(message.get("id") or ""),
            "role": role if role in {"user", "assistant", "system"} else "assistant",
            "content": str(content or ""),
            "created_at": message.get("sent"),
            "name": "Patient" if role == "user" else "Assistant",
        }

    def create_zep_projection_task(self, *, patient_id: str, thread_id: str, user_message_id: str, assistant_message_id: str, request_id: str) -> dict[str, Any]:
        identifier = f"chat:{request_id}"
        return self.client.conditional_upsert({
            "resourceType": "Task",
            "status": "requested",
            "intent": "order",
            "identifier": [{"system": TASK_SYSTEM, "value": identifier}],
            "code": {"coding": [{"system": CODE_SYSTEM, "code": "zep-chat-projection"}], "text": "zep-chat-projection"},
            "focus": {"reference": f"Communication/{assistant_message_id}"},
            "for": {"reference": f"Patient/{patient_id}"},
            "authoredOn": utc_now(),
            "lastModified": utc_now(),
            "input": [
                {"type": {"text": "thread"}, "valueReference": {"reference": f"Communication/{thread_id}"}},
                {"type": {"text": "user-message"}, "valueReference": {"reference": f"Communication/{user_message_id}"}},
            ],
        }, identifier=f"{TASK_SYSTEM}|{identifier}")

    def pending_projection_tasks(self, *, count: int = 50) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for status in ("requested", "failed"):
            rows.extend(self.client.search("Task", {"status": status, "code": f"{CODE_SYSTEM}|zep-chat-projection", "_count": count}))
            rows.extend(self.client.search("Task", {"status": status, "code": f"{CODE_SYSTEM}|document-processing", "_count": count}))
        return rows[:count]

    def complete_projection_task(self, task: dict[str, Any], *, error: str | None = None, episode_count: int = 0) -> dict[str, Any]:
        return self.finish_task(task, error=error, episode_count=episode_count)

    def abandon_projection_task(self, task: dict[str, Any], *, reason: str) -> dict[str, Any]:
        """Make a structurally invalid task terminal so the polling worker does not loop forever."""
        return self.client.update({
            **task,
            "status": "entered-in-error",
            "statusReason": {"text": reason[:500]},
            "lastModified": utc_now(),
        })


def repository() -> MedplumRepository:
    return MedplumRepository()
