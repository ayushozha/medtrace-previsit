"""FHIR R4 repository and MedTrace-domain mappings for Medplum."""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import uuid
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import urlencode, urlparse

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
CHECKLIST_SYSTEM = f"{IDENTIFIER_BASE}/doctor-checklist"
IMAGING_STUDY_SYSTEM = f"{IDENTIFIER_BASE}/imaging-study"
IMAGING_DOCUMENT_SYSTEM = f"{IDENTIFIER_BASE}/imaging-document"
DIAGNOSTIC_REPORT_SYSTEM = f"{IDENTIFIER_BASE}/diagnostic-report"
CONSULTATION_SYSTEM = f"{IDENTIFIER_BASE}/consultation"
REPORT_REVIEW_TASK_SYSTEM = f"{DIAGNOSTIC_REPORT_SYSTEM}/review-task"
REPORT_EXTENSION_BASE = "https://medtrace.local/fhir/StructureDefinition/imaging-report"
AI_UNVERIFIED_TAG = {"system": TAG_SYSTEM, "code": "ai-extracted-unverified", "display": "AI extracted — unverified"}
PATIENT_DOCUMENT_KINDS = frozenset({"clinical_pdf", "radiology_note", "conversation_note", "dicom"})


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _identifiers(resource: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in resource.get("identifier") or [] if isinstance(item, dict)]


def identifier_value(resource: dict[str, Any], system: str) -> str | None:
    for item in _identifiers(resource):
        if item.get("system") == system and item.get("value"):
            return str(item["value"])
    return None


def binary_id_from_url(url: str | None) -> str | None:
    """Return a Binary id from either FHIR-relative or Medplum storage URLs."""
    raw = str(url or "")
    if raw.startswith("Binary/"):
        return raw.removeprefix("Binary/").split("/", 1)[0] or None
    parts = [part for part in urlparse(raw).path.split("/") if part]
    if "storage" in parts:
        index = parts.index("storage")
        if len(parts) > index + 1:
            return parts[index + 1]
    if "Binary" in parts:
        index = parts.index("Binary")
        if len(parts) > index + 1:
            return parts[index + 1]
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
        existing = self.find_patient_by_zep(zep_user_id)
        resource: dict[str, Any] = dict(existing or {})
        resource.update({"resourceType": "Patient", "active": True})

        identifiers = [
            dict(item)
            for item in _identifiers(resource)
            if item.get("system") not in {ZEP_USER_SYSTEM, LEGACY_CHART_SYSTEM}
        ]
        identifiers.append({"system": ZEP_USER_SYSTEM, "value": zep_user_id})
        if legacy_chart_id:
            identifiers.append({"system": LEGACY_CHART_SYSTEM, "value": legacy_chart_id})
        elif existing:
            legacy = identifier_value(existing, LEGACY_CHART_SYSTEM)
            if legacy:
                identifiers.append({"system": LEGACY_CHART_SYSTEM, "value": legacy})
        resource["identifier"] = identifiers
        resource["name"] = [{"text": display_name}]
        if sex is not None or not existing:
            resource["gender"] = _fhir_gender(sex)
        if birth_date:
            resource["birthDate"] = birth_date
        if meta_tags:
            meta = dict(resource.get("meta") or {})
            existing_tags = [
                dict(item)
                for item in meta.get("tag") or []
                if isinstance(item, dict)
                and not any(
                    item.get("system") == tag.get("system") and item.get("code") == tag.get("code")
                    for tag in meta_tags
                )
            ]
            meta["tag"] = [*existing_tags, *meta_tags]
            resource["meta"] = meta
        if primary_doctor:
            practitioner_role = self._ensure_practitioner_role(primary_doctor)
            resource["generalPractitioner"] = [
                {"reference": f"PractitionerRole/{practitioner_role['id']}", "display": primary_doctor}
            ]
        if existing:
            return self.client.update(resource)
        return self.client.conditional_upsert(resource, identifier=f"{ZEP_USER_SYSTEM}|{zep_user_id}")

    def update_patient(self, patient_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
        """Patch canonical Patient demographics without dropping unrelated FHIR fields."""
        patient = self.get_patient(patient_id)
        if not patient:
            return None
        resource = dict(patient)
        if "display_name" in updates:
            display_name = str(updates.get("display_name") or "").strip()
            if display_name:
                resource["name"] = [{"text": display_name}]
        if "dob" in updates:
            dob = str(updates.get("dob") or "").strip()
            if dob:
                resource["birthDate"] = dob
            else:
                resource.pop("birthDate", None)
        if "sex" in updates:
            resource["gender"] = _fhir_gender(updates.get("sex"))
        if "primary_doctor" in updates:
            doctor = str(updates.get("primary_doctor") or "").strip()
            if doctor:
                role = self._ensure_practitioner_role(doctor)
                resource["generalPractitioner"] = [
                    {"reference": f"PractitionerRole/{role['id']}", "display": doctor}
                ]
            else:
                resource.pop("generalPractitioner", None)
        return self.client.update(resource)

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
        grouped: dict[tuple[str, str], list[tuple[tuple[int, str], dict[str, Any]]]] = defaultdict(list)
        previous: list[dict[str, Any]] = []
        for row in resources.get("MedicationStatement") or []:
            raw_status = str(row.get("status") or "active").casefold()
            if raw_status == "entered-in-error":
                continue
            dosage = (row.get("dosage") or [{}])[0]
            text = str(dosage.get("text") or "") if isinstance(dosage, dict) else ""
            dose = next(iter(re.findall(r"\b\d+(?:\.\d+)?\s*(?:mg|mcg|g|mL|units?)\b", text, re.I)), None)
            frequency = text.replace(dose, "").strip(" ,-;") if dose else text or None
            ref = f"MedicationStatement/{row.get('id')}"
            item = {
                "name": _coding_text(row.get("medicationCodeableConcept")) or "Unspecified medication",
                "dose": dose,
                "frequency": frequency,
                "status": "Previous" if raw_status in {"completed", "stopped"} else "Active",
                "start": _resource_date(row),
                "end": (row.get("effectivePeriod") or {}).get("end") if isinstance(row.get("effectivePeriod"), dict) else None,
                "verification_status": _verification(row),
                "source_document_id": sources.get(ref),
            }
            if item["status"] == "Previous":
                previous.append(item)
                continue
            key = (item["name"].strip().casefold(), item["status"])
            recency = str((row.get("meta") or {}).get("lastUpdated") or _resource_date(row) or "")
            grouped[key].append(((item["verification_status"] == "verified", recency), item))

        out: list[dict[str, Any]] = []
        for candidates in grouped.values():
            candidates.sort(key=lambda candidate: candidate[0], reverse=True)
            item = dict(candidates[0][1])
            trust_rank = candidates[0][0][0]
            rows = [candidate[1] for candidate in candidates if candidate[0][0] == trust_rank]
            item["name"] = next((row["name"] for row in rows if not row["name"].islower()), item["name"])
            for field in ("dose", "frequency", "source_document_id"):
                item[field] = item.get(field) or next((row.get(field) for row in rows if row.get(field)), None)
            starts = [str(row["start"]) for row in rows if row.get("start")]
            if item["status"] == "Active" and starts:
                item["start"] = min(starts)
            out.append(item)
        previous.sort(key=lambda item: (item["name"].casefold(), str(item.get("start") or ""), str(item.get("end") or "")))
        return out + previous

    def allergy_views(self, resources: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
        sources = self._provenance_sources(resources)
        grouped: dict[tuple[str, str], list[tuple[tuple[int, str], dict[str, Any], str]]] = defaultdict(list)
        for row in resources.get("AllergyIntolerance") or []:
            verification = _coding_text(row.get("verificationStatus")).casefold()
            if verification == "entered-in-error":
                continue
            reaction = ""
            reactions = row.get("reaction") or []
            if reactions and isinstance(reactions[0], dict):
                manifestations = reactions[0].get("manifestation") or []
                if manifestations:
                    reaction = _coding_text(manifestations[0])
            ref = f"AllergyIntolerance/{row.get('id')}"
            item = {
                "allergen": _coding_text(row.get("code")) or "Unspecified allergen",
                "reaction": reaction or None,
                "source": f"Document {sources[ref]}" if sources.get(ref) else None,
                "verification_status": _verification(row),
                "source_document_id": sources.get(ref),
            }
            clinical_status = _coding_text(row.get("clinicalStatus")).casefold() or "active"
            key = (item["allergen"].strip().casefold(), clinical_status)
            verification_rank = int(item["verification_status"] == "verified")
            recency = str((row.get("meta") or {}).get("lastUpdated") or _resource_date(row) or "")
            grouped[key].append(((verification_rank, recency), item, verification))

        out: list[dict[str, Any]] = []
        for candidates in grouped.values():
            candidates.sort(key=lambda candidate: candidate[0], reverse=True)
            if candidates[0][2] == "refuted":
                continue
            item = dict(candidates[0][1])
            trust_rank = candidates[0][0][0]
            rows = [
                candidate[1]
                for candidate in candidates
                if candidate[0][0] == trust_rank and candidate[2] != "refuted"
            ]
            item["allergen"] = next(
                (row["allergen"] for row in rows if not row["allergen"].islower()), item["allergen"]
            )
            for field in ("reaction", "source", "source_document_id"):
                item[field] = item.get(field) or next((row.get(field) for row in rows if row.get(field)), None)
            out.append(item)
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

    def checklist_views(
        self,
        *,
        patient_id: str,
        resources: dict[str, list[dict[str, Any]]],
        suggestions: list[str],
    ) -> list[dict[str, Any]]:
        existing: dict[str, dict[str, Any]] = {}
        for task in resources.get("Task") or []:
            item_id = identifier_value(task, CHECKLIST_SYSTEM)
            if item_id:
                existing[item_id] = task
        out: list[dict[str, Any]] = []
        suggested_ids: set[str] = set()
        for text in suggestions:
            item_id = uuid.uuid5(uuid.NAMESPACE_URL, f"{patient_id}:checklist:{text}").hex
            suggested_ids.add(item_id)
            task = existing.get(item_id)
            out.append(
                {
                    "id": item_id,
                    "text": str((task or {}).get("description") or text),
                    "done": bool(task and task.get("status") == "completed"),
                    "agent_note": _coding_text((task or {}).get("businessStatus")) or None,
                }
            )
        for item_id, task in existing.items():
            if item_id in suggested_ids:
                continue
            out.append(
                {
                    "id": item_id,
                    "text": str(task.get("description") or "Clinical review item"),
                    "done": task.get("status") == "completed",
                    "agent_note": _coding_text(task.get("businessStatus")) or None,
                }
            )
        return out

    def upsert_checklist_item(
        self,
        *,
        patient_id: str,
        item_id: str,
        text: str,
        done: bool,
        agent_note: str | None = None,
    ) -> dict[str, Any]:
        identifier = f"{CHECKLIST_SYSTEM}|{item_id}"
        existing = self.client.search_one("Task", {"identifier": identifier})
        if existing and _reference_id(existing.get("for"), "Patient") != patient_id:
            raise ValueError("Checklist item belongs to a different patient.")
        return self.client.conditional_upsert(
            {
                "resourceType": "Task",
                "identifier": [{"system": CHECKLIST_SYSTEM, "value": item_id}],
                "status": "completed" if done else "requested",
                "intent": "plan",
                "code": {
                    "coding": [{"system": CODE_SYSTEM, "code": "doctor-checklist"}],
                    "text": "doctor-checklist",
                },
                "for": {"reference": f"Patient/{patient_id}"},
                "authoredOn": utc_now(),
                "lastModified": utc_now(),
                "description": text[:1000],
                **({"businessStatus": {"text": agent_note[:500]}} if agent_note else {}),
            },
            identifier=identifier,
        )

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
        binary_id = binary_id_from_url(binary_ref)
        document_kind = (
            "dicom"
            if identifier_value(doc, IMAGING_DOCUMENT_SYSTEM)
            else _coding_text(doc.get("type")) or "clinical_pdf"
        )
        return {
            "doc_id": str(doc.get("id") or ""),
            "filename": str(attachment.get("title") or "file"),
            "document_kind": document_kind,
            "extract_mode": _tag_value(doc, f"{TAG_SYSTEM}/extract-mode"),
            "episode_count": episode_count,
            "storage_url": attachment.get("url"),
            "storage_key": f"Binary/{binary_id}" if binary_id else None,
            "storage_bucket": "medplum",
            "uploaded_at": str(doc.get("date") or (doc.get("meta") or {}).get("lastUpdated") or ""),
            "status": status,
            "review_status": "Needs review" if _tag(doc, "ai-extracted-unverified") else "Approved",
            "processing_error": _coding_text((task or {}).get("statusReason")) or None,
        }

    def list_documents(self, patient_id: str, resources: dict[str, list[dict[str, Any]]] | None = None) -> list[dict[str, Any]]:
        data = resources or self.clinical_resources(patient_id)
        docs = [
            view
            for doc in data.get("DocumentReference") or []
            if (view := self.document_view(doc, data.get("Task")))["document_kind"]
            in PATIENT_DOCUMENT_KINDS
        ]
        docs.sort(key=lambda item: item["uploaded_at"], reverse=True)
        return docs

    def json_document_payloads(
        self,
        resources: dict[str, list[dict[str, Any]]],
        *,
        document_type: str,
    ) -> list[dict[str, Any]]:
        """Decode bounded inline JSON documents for canonical clinical context."""
        payloads: list[dict[str, Any]] = []
        for doc in resources.get("DocumentReference") or []:
            if _coding_text(doc.get("type")) != document_type:
                continue
            for content in doc.get("content") or []:
                attachment = content.get("attachment") if isinstance(content, dict) else None
                if not isinstance(attachment, dict) or attachment.get("contentType") != "application/json":
                    continue
                encoded = attachment.get("data")
                if not isinstance(encoded, str):
                    continue
                try:
                    value = json.loads(base64.b64decode(encoded, validate=True))
                except (binascii.Error, ValueError, TypeError, json.JSONDecodeError):
                    continue
                if isinstance(value, dict):
                    payloads.append(
                        {
                            "document_id": str(doc.get("id") or ""),
                            "date": str(doc.get("date") or ""),
                            "payload": value,
                        }
                    )
                    break
        payloads.sort(key=lambda item: item["date"], reverse=True)
        return payloads[:10]

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
        uploaded_at: str | None = None,
        unverified: bool = True,
        tags: list[str] | None = None,
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
                if not task:
                    task = self._create_document_task(patient_id, str(existing["id"]))
                return existing, task
        binary = self.client.create_binary(
            data,
            content_type=content_type,
            security_context=f"Patient/{patient_id}",
        )
        doc_identifier = source_doc_id or uuid.uuid4().hex
        meta_tags = [{"system": f"{TAG_SYSTEM}/extract-mode", "code": extract_mode}]
        meta_tags.extend({"system": TAG_SYSTEM, "code": tag} for tag in (tags or []))
        if unverified:
            meta_tags.append(AI_UNVERIFIED_TAG)
        doc = self.client.create({
            "resourceType": "DocumentReference",
            "status": "current",
            "identifier": [{"system": DOCUMENT_SYSTEM, "value": doc_identifier}],
            "meta": {"tag": meta_tags},
            "type": {"text": document_kind},
            "subject": {"reference": f"Patient/{patient_id}"},
            "date": uploaded_at or utc_now(),
            "content": [{"attachment": {"contentType": content_type, "url": f"Binary/{binary['id']}", "title": filename, "size": len(data)}}],
        })
        task = self._create_document_task(patient_id, str(doc["id"]))
        return doc, task

    def _create_document_task(self, patient_id: str, doc_id: str) -> dict[str, Any]:
        return self.client.create({
            "resourceType": "Task",
            "status": "in-progress",
            "intent": "order",
            "code": {"coding": [{"system": CODE_SYSTEM, "code": "document-processing", "display": "document-processing"}], "text": "document-processing"},
            "focus": {"reference": f"DocumentReference/{doc_id}"},
            "for": {"reference": f"Patient/{patient_id}"},
            "authoredOn": utc_now(),
            "lastModified": utc_now(),
        })

    def attach_extracted_text(self, doc: dict[str, Any], task: dict[str, Any], text: str) -> tuple[dict[str, Any], dict[str, Any]]:
        subject = _reference_id(doc.get("subject"), "Patient")
        binary = self.client.create_binary(
            text.encode("utf-8"),
            content_type="text/plain; charset=utf-8",
            security_context=f"Patient/{subject}" if subject else None,
        )
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

    # ---- imaging ---------------------------------------------------------

    def upsert_imaging_study(
        self,
        *,
        patient_id: str,
        study_id: str,
        paths: list[Any],
        synthetic: bool = False,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        """Register DICOM metadata and one patient-scoped source object in Medplum.

        The ImagingStudy includes every supplied DICOM instance.  A representative
        DICOM file is stored as Binary + DocumentReference; the complete series remains
        in the imaging object store used by the viewer.
        """
        from pathlib import Path

        from medtrace_agent.imaging.fhir import build_imaging_study

        patient = self.get_patient(patient_id)
        if not patient:
            raise ValueError(f"Patient/{patient_id} does not exist.")
        study_resource, representative, source_metadata = build_imaging_study(
            paths=[Path(path) for path in paths],
            patient_id=patient_id,
            patient_display=_display_name(patient),
            study_id=study_id,
            identifier_system=IMAGING_STUDY_SYSTEM,
            synthetic=synthetic,
            tag_system=TAG_SYSTEM,
        )
        imaging_study = self.client.conditional_upsert(
            study_resource,
            identifier=f"{IMAGING_STUDY_SYSTEM}|{study_id}",
        )

        document = self.client.search_one(
            "DocumentReference",
            {"identifier": f"{IMAGING_DOCUMENT_SYSTEM}|{study_id}"},
        )
        if not document:
            source_bytes = representative.read_bytes()
            binary = self.client.create_binary(
                source_bytes,
                content_type="application/dicom",
                security_context=f"Patient/{patient_id}",
            )
            tags = [{"system": TAG_SYSTEM, "code": "dicom-representative"}]
            if synthetic:
                tags.append({"system": TAG_SYSTEM, "code": "synthetic"})
            document = self.client.create(
                {
                    "resourceType": "DocumentReference",
                    "status": "current",
                    "identifier": [{"system": IMAGING_DOCUMENT_SYSTEM, "value": study_id}],
                    "meta": {"tag": tags},
                    "type": {
                        "coding": [
                            {
                                "system": "http://loinc.org",
                                "code": "18748-4",
                                "display": "Diagnostic imaging study",
                            }
                        ],
                        "text": "dicom",
                    },
                    "subject": {"reference": f"Patient/{patient_id}", "display": _display_name(patient)},
                    "date": utc_now(),
                    "description": (
                        "Representative DICOM object. Complete series metadata is in "
                        f"ImagingStudy/{imaging_study['id']}; complete pixels are in the local demo imaging store."
                    ),
                    "content": [
                        {
                            "attachment": {
                                "contentType": "application/dicom",
                                "url": f"Binary/{binary['id']}",
                                "title": representative.name,
                                "size": len(source_bytes),
                            }
                        }
                    ],
                    "context": {"related": [{"reference": f"ImagingStudy/{imaging_study['id']}"}]},
                }
            )
        return imaging_study, document, source_metadata

    def list_imaging_studies(self, patient_id: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"_count": 500, "_sort": "-_lastUpdated"}
        if patient_id:
            params["subject"] = f"Patient/{patient_id}"
        return self.client.search("ImagingStudy", params)

    def imaging_document(self, study_id: str) -> dict[str, Any] | None:
        return self.client.search_one(
            "DocumentReference",
            {"identifier": f"{IMAGING_DOCUMENT_SYSTEM}|{study_id}"},
        )

    @staticmethod
    def _report_extension(code: str, value_key: str, value: Any) -> dict[str, Any]:
        return {"url": f"{REPORT_EXTENSION_BASE}/{code}", value_key: value}

    @staticmethod
    def _extension_value(resource: dict[str, Any], code: str) -> Any:
        url = f"{REPORT_EXTENSION_BASE}/{code}"
        for extension in resource.get("extension") or []:
            if not isinstance(extension, dict) or extension.get("url") != url:
                continue
            for key, value in extension.items():
                if key.startswith("value"):
                    return value
        return None

    def upsert_diagnostic_report(
        self,
        *,
        patient_id: str,
        study_id: str,
        report: dict[str, Any],
    ) -> dict[str, Any]:
        imaging_study = self.client.search_one(
            "ImagingStudy",
            {"identifier": f"{IMAGING_STUDY_SYSTEM}|{study_id}"},
        )
        if not imaging_study:
            raise ValueError(f"Imaging study {study_id} is not registered in Medplum.")
        existing = self.client.search_one(
            "DiagnosticReport",
            {"identifier": f"{DIAGNOSTIC_REPORT_SYSTEM}|{study_id}"},
        )
        payload = json.dumps(report, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        existing_binary_id = binary_id_from_url(
            (((existing or {}).get("presentedForm") or [{}])[0].get("url"))
        )
        if existing_binary_id:
            binary = self.client.update_binary(
                existing_binary_id,
                payload,
                content_type="application/json",
                security_context=f"Patient/{patient_id}",
            )
        else:
            binary = self.client.create_binary(
                payload,
                content_type="application/json",
                security_context=f"Patient/{patient_id}",
            )
        resource = dict(existing or {})
        resource.update(
            {
                "resourceType": "DiagnosticReport",
                "identifier": [{"system": DIAGNOSTIC_REPORT_SYSTEM, "value": study_id}],
                "status": "preliminary",
                "category": [{"text": "Imaging"}],
                "code": {
                    "coding": [
                        {
                            "system": "http://loinc.org",
                            "code": "18748-4",
                            "display": "Diagnostic imaging study",
                        }
                    ],
                    "text": "AI-assisted imaging draft",
                },
                "subject": {"reference": f"Patient/{patient_id}"},
                "issued": utc_now(),
                "imagingStudy": [{"reference": f"ImagingStudy/{imaging_study['id']}"}],
                "conclusion": str(report.get("impression") or report.get("summary") or "")[:10000],
                "presentedForm": [
                    {
                        "contentType": "application/json",
                        "url": f"Binary/{binary['id']}",
                        "title": f"{study_id} AI imaging draft.json",
                        "size": len(payload),
                    }
                ],
                "extension": [
                    self._report_extension("summary", "valueString", str(report.get("summary") or "")),
                    self._report_extension("findings", "valueString", str(report.get("findings") or "")),
                    self._report_extension("impression", "valueString", str(report.get("impression") or "")),
                    self._report_extension("recommendation", "valueString", str(report.get("recommendation") or "")),
                    self._report_extension("confidence", "valueDecimal", float(report.get("confidence") or 0)),
                    self._report_extension("source", "valueCode", str(report.get("source") or "mock")),
                    self._report_extension("review-decision", "valueCode", "unreviewed"),
                ],
            }
        )
        meta = dict(resource.get("meta") or {})
        meta["tag"] = [
            *[
                dict(tag)
                for tag in meta.get("tag") or []
                if isinstance(tag, dict) and tag.get("code") != "ai-extracted-unverified"
            ],
            AI_UNVERIFIED_TAG,
        ]
        resource["meta"] = meta
        if existing:
            return self.client.update(resource)
        return self.client.conditional_upsert(
            resource,
            identifier=f"{DIAGNOSTIC_REPORT_SYSTEM}|{study_id}",
        )

    def diagnostic_report_for_study(self, study_id: str) -> dict[str, Any] | None:
        return self.client.search_one(
            "DiagnosticReport",
            {"identifier": f"{DIAGNOSTIC_REPORT_SYSTEM}|{study_id}"},
        )

    def diagnostic_report_view(self, report: dict[str, Any]) -> dict[str, Any]:
        return {
            "summary": str(self._extension_value(report, "summary") or ""),
            "findings": str(self._extension_value(report, "findings") or ""),
            "impression": str(self._extension_value(report, "impression") or report.get("conclusion") or ""),
            "recommendation": str(self._extension_value(report, "recommendation") or ""),
            "confidence": float(self._extension_value(report, "confidence") or 0),
            "source": str(self._extension_value(report, "source") or "mock"),
            "fhir_diagnostic_report_id": str(report.get("id") or ""),
        }

    def review_diagnostic_report(
        self,
        *,
        patient_id: str,
        study_id: str,
        decision: str,
        reviewer_id: str,
        reviewer_name: str,
        note: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        report = self.diagnostic_report_for_study(study_id)
        if not report:
            raise ValueError(f"No diagnostic report exists for imaging study {study_id}.")
        updated = dict(report)
        updated["status"] = "final" if decision == "accepted" else "preliminary"
        extensions = [
            dict(extension)
            for extension in updated.get("extension") or []
            if isinstance(extension, dict)
            and extension.get("url")
            not in {
                f"{REPORT_EXTENSION_BASE}/review-decision",
                f"{REPORT_EXTENSION_BASE}/review-note",
                f"{REPORT_EXTENSION_BASE}/reviewer-id",
                f"{REPORT_EXTENSION_BASE}/reviewer-name",
                f"{REPORT_EXTENSION_BASE}/reviewed-at",
            }
        ]
        reviewed_at = utc_now()
        extensions.append(self._report_extension("review-decision", "valueCode", decision))
        extensions.extend(
            [
                self._report_extension("reviewer-id", "valueString", reviewer_id),
                self._report_extension("reviewer-name", "valueString", reviewer_name),
                self._report_extension("reviewed-at", "valueDateTime", reviewed_at),
            ]
        )
        if note:
            extensions.append(self._report_extension("review-note", "valueString", note[:2000]))
        updated["extension"] = extensions
        meta = dict(updated.get("meta") or {})
        tags = [
            dict(tag)
            for tag in meta.get("tag") or []
            if isinstance(tag, dict)
            and not (decision == "accepted" and tag.get("code") == "ai-extracted-unverified")
        ]
        if decision != "accepted" and not any(
            tag.get("system") == TAG_SYSTEM and tag.get("code") == "ai-extracted-unverified"
            for tag in tags
        ):
            tags.append(AI_UNVERIFIED_TAG)
        meta["tag"] = tags
        updated["meta"] = meta
        updated = self.client.update(updated)

        task_status = "completed" if decision == "accepted" else "requested"
        task = self.client.conditional_upsert(
            {
                "resourceType": "Task",
                "identifier": [{"system": REPORT_REVIEW_TASK_SYSTEM, "value": study_id}],
                "status": task_status,
                "intent": "order",
                "code": {"text": "clinician-imaging-report-review"},
                "businessStatus": {"text": decision},
                "focus": {"reference": f"DiagnosticReport/{updated['id']}"},
                "for": {"reference": f"Patient/{patient_id}"},
                "owner": {
                    "identifier": {
                        "system": f"{IDENTIFIER_BASE}/imaging-reviewer",
                        "value": reviewer_id,
                    },
                    "display": reviewer_name,
                },
                "authoredOn": reviewed_at,
                "lastModified": reviewed_at,
                **({"description": note[:1000]} if note else {}),
            },
            identifier=f"{REPORT_REVIEW_TASK_SYSTEM}|{study_id}",
        )
        return updated, task

    def report_review(self, report: dict[str, Any]) -> tuple[str, str | None]:
        decision = str(self._extension_value(report, "review-decision") or "unreviewed")
        note = self._extension_value(report, "review-note")
        return decision, str(note) if note else None

    def report_reviewer(self, report: dict[str, Any]) -> dict[str, str | None]:
        return {
            "reviewer_id": str(self._extension_value(report, "reviewer-id") or "") or None,
            "reviewer_name": str(self._extension_value(report, "reviewer-name") or "") or None,
            "reviewed_at": str(self._extension_value(report, "reviewed-at") or "") or None,
        }

    # ---- consultations ---------------------------------------------------

    def _upsert_consultation_document(
        self,
        *,
        patient_id: str,
        encounter_id: str,
        consultation_id: str,
        artifact: str,
        data: bytes,
        filename: str,
        content_type: str,
    ) -> dict[str, Any]:
        source_id = f"consultation:{patient_id}:{consultation_id}:{artifact}"
        existing = self.client.search_one(
            "DocumentReference",
            {"identifier": f"{DOCUMENT_SYSTEM}|{source_id}"},
        )
        existing_attachment = ((existing or {}).get("content") or [{}])[0].get("attachment") or {}
        existing_binary_id = binary_id_from_url(existing_attachment.get("url"))
        binary = (
            self.client.update_binary(
                existing_binary_id,
                data,
                content_type=content_type,
                security_context=f"Patient/{patient_id}",
            )
            if existing_binary_id
            else self.client.create_binary(
                data,
                content_type=content_type,
                security_context=f"Patient/{patient_id}",
            )
        )
        resource = dict(existing or {})
        resource.update(
            {
                "resourceType": "DocumentReference",
                "status": "current",
                "identifier": [{"system": DOCUMENT_SYSTEM, "value": source_id}],
                "meta": {
                    "tag": [
                        {"system": TAG_SYSTEM, "code": "consultation"},
                        {"system": TAG_SYSTEM, "code": artifact},
                    ]
                },
                "type": {"text": "conversation_note"},
                "subject": {"reference": f"Patient/{patient_id}"},
                "date": utc_now(),
                "content": [
                    {
                        "attachment": {
                            "contentType": content_type,
                            "url": f"Binary/{binary['id']}",
                            "title": filename,
                            "size": len(data),
                        }
                    }
                ],
                "context": {"encounter": [{"reference": f"Encounter/{encounter_id}"}]},
            }
        )
        if existing:
            return self.client.update(resource)
        return self.client.conditional_upsert(resource, identifier=f"{DOCUMENT_SYSTEM}|{source_id}")

    def upsert_consultation(
        self,
        *,
        patient_id: str,
        consultation_id: str,
        transcript: str,
        report: str,
        duration: str | None = None,
        recorded_at: str | None = None,
        audio: bytes | None = None,
        audio_content_type: str = "audio/wav",
    ) -> dict[str, Any]:
        """Persist a voice consultation as Encounter plus patient-scoped artifacts."""
        patient = self.get_patient(patient_id)
        if not patient:
            raise ValueError(f"Patient/{patient_id} does not exist.")
        now = recorded_at or utc_now()
        duration_seconds: int | None = None
        if duration:
            try:
                minutes, seconds = duration.split(":", 1)
                duration_seconds = max(0, int(minutes) * 60 + int(seconds))
            except (ValueError, AttributeError):
                duration_seconds = None
        encounter_identifier = f"{patient_id}:{consultation_id}"
        encounter_full_url = f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, encounter_identifier)}"
        encounter_resource = {
            "resourceType": "Encounter",
            "identifier": [{"system": CONSULTATION_SYSTEM, "value": encounter_identifier}],
            "status": "finished",
            "class": {
                "system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
                "code": "AMB",
                "display": "ambulatory",
            },
            "type": [{"text": "Voice consultation"}],
            "subject": {"reference": f"Patient/{patient_id}", "display": _display_name(patient)},
            "period": {"end": now},
            **(
                {
                    "length": {
                        "value": duration_seconds,
                        "unit": "seconds",
                        "system": "http://unitsofmeasure.org",
                        "code": "s",
                    }
                }
                if duration_seconds is not None
                else {}
            ),
        }
        entries: list[dict[str, Any]] = [
            {
                "fullUrl": encounter_full_url,
                "resource": encounter_resource,
                "request": {
                    "method": "PUT",
                    "url": f"Encounter?identifier={CONSULTATION_SYSTEM}|{encounter_identifier}",
                },
            }
        ]
        artifacts = [
            ("transcript", transcript.encode("utf-8"), "transcript.txt", "text/plain; charset=utf-8"),
            ("report", report.encode("utf-8"), "clinical-report.md", "text/markdown; charset=utf-8"),
        ]
        if audio is not None:
            artifacts.append(("audio", audio, "consultation-audio", audio_content_type))
        for artifact, data, filename, content_type in artifacts:
            source_id = f"consultation:{patient_id}:{consultation_id}:{artifact}"
            binary_id = uuid.uuid5(uuid.NAMESPACE_URL, f"{source_id}:binary").hex
            entries.extend(
                [
                    {
                        "resource": {
                            "resourceType": "Binary",
                            "id": binary_id,
                            "contentType": content_type,
                            "securityContext": {"reference": f"Patient/{patient_id}"},
                            "data": base64.b64encode(data).decode("ascii"),
                        },
                        "request": {"method": "PUT", "url": f"Binary/{binary_id}"},
                    },
                    {
                        "resource": {
                            "resourceType": "DocumentReference",
                            "status": "current",
                            "identifier": [{"system": DOCUMENT_SYSTEM, "value": source_id}],
                            "meta": {"tag": [
                                {"system": TAG_SYSTEM, "code": "consultation"},
                                {"system": TAG_SYSTEM, "code": artifact},
                            ]},
                            "type": {"text": "conversation_note"},
                            "subject": {"reference": f"Patient/{patient_id}"},
                            "date": utc_now(),
                            "content": [{"attachment": {
                                "contentType": content_type,
                                "url": f"Binary/{binary_id}",
                                "title": filename,
                                "size": len(data),
                            }}],
                            "context": {"encounter": [{"reference": encounter_full_url}]},
                        },
                        "request": {
                            "method": "PUT",
                            "url": f"DocumentReference?identifier={DOCUMENT_SYSTEM}|{source_id}",
                        },
                    },
                ]
            )
        response = self.client.transaction(entries)

        def response_id(index: int, resource_type: str, identifier: str) -> str:
            response_entry = (response.get("entry") or [{}])[index]
            location = str((response_entry.get("response") or {}).get("location") or "")
            if location.startswith(f"{resource_type}/"):
                return location.removeprefix(f"{resource_type}/").split("/", 1)[0]
            found = self.client.search_one(resource_type, {"identifier": identifier})
            return str((found or {}).get("id") or "")

        encounter_id = response_id(
            0,
            "Encounter",
            f"{CONSULTATION_SYSTEM}|{encounter_identifier}",
        )
        documents = {
            artifact: response_id(
                2 + index * 2,
                "DocumentReference",
                f"{DOCUMENT_SYSTEM}|consultation:{patient_id}:{consultation_id}:{artifact}",
            )
            for index, (artifact, *_rest) in enumerate(artifacts)
        }
        return {"encounter_id": encounter_id, "document_ids": documents}

    def consultation_views(self, patient_id: str) -> list[dict[str, Any]]:
        """Reconstruct voice-session history from canonical patient-scoped FHIR resources."""
        resources = self.clinical_resources(patient_id)
        documents_by_encounter: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for document in resources.get("DocumentReference") or []:
            if not _tag(document, "consultation"):
                continue
            for encounter in (document.get("context") or {}).get("encounter") or []:
                encounter_id = _reference_id(encounter, "Encounter")
                if encounter_id:
                    documents_by_encounter[encounter_id].append(document)

        out: list[dict[str, Any]] = []
        for encounter in resources.get("Encounter") or []:
            raw_id = identifier_value(encounter, CONSULTATION_SYSTEM)
            encounter_id = str(encounter.get("id") or "")
            if not raw_id or not encounter_id:
                continue
            consultation_id = raw_id.removeprefix(f"{patient_id}:")
            row: dict[str, Any] = {
                "id": consultation_id,
                "patient_id": patient_id,
                "timestamp": str((encounter.get("period") or {}).get("end") or ""),
                "duration": "0:00",
                "transcript": "",
                "report": "",
                "audio_base64": "",
            }
            length = encounter.get("length") or {}
            if isinstance(length, dict) and length.get("value") is not None:
                seconds = max(0, int(float(length["value"])))
                row["duration"] = f"{seconds // 60}:{seconds % 60:02d}"
            for document in documents_by_encounter.get(encounter_id, []):
                artifact = next(
                    (name for name in ("transcript", "report", "audio") if _tag(document, name)),
                    None,
                )
                attachment = ((document.get("content") or [{}])[0].get("attachment") or {})
                binary_id = binary_id_from_url(attachment.get("url"))
                if not artifact or not binary_id:
                    continue
                data = self.client.read_binary(binary_id)
                if artifact == "audio":
                    content_type = str(attachment.get("contentType") or "application/octet-stream")
                    row["audio_base64"] = (
                        f"data:{content_type};base64,{base64.b64encode(data).decode('ascii')}"
                    )
                else:
                    row[artifact] = data.decode("utf-8", errors="replace")
            out.append(row)
        return sorted(out, key=lambda row: row["timestamp"], reverse=True)

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
        thread_id = str(thread["id"])
        identifier = f"{thread_id}:{request_id}:{role}"
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

    def message_by_request(self, thread_id: str, request_id: str, role: str) -> dict[str, Any] | None:
        scoped = self.client.search_one(
            "Communication",
            {"identifier": f"{MESSAGE_REQUEST_SYSTEM}|{thread_id}:{request_id}:{role}"},
        )
        if scoped:
            return scoped
        legacy = self.client.search_one(
            "Communication",
            {"identifier": f"{MESSAGE_REQUEST_SYSTEM}|{request_id}:{role}"},
        )
        expected = f"Communication/{thread_id}"
        return legacy if any(
            isinstance(parent, dict) and parent.get("reference") == expected
            for parent in (legacy or {}).get("partOf") or []
        ) else None

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
        identifier = f"chat:{thread_id}:{request_id}"
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
            rows.extend(self.client.search("Task", {"status": status, "code": f"{CODE_SYSTEM}|zep-demo-projection", "_count": count}))
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
