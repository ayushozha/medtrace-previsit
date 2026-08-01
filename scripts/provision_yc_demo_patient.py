"""Idempotently provision the synthetic Jane Doe Medplum demo chart."""

from __future__ import annotations

import os
import sys
from typing import Any

from medtrace_agent.env import load_repo_env
from medtrace_agent.integrations.medplum import (
    DEMO_STEDI_TAG_CODE,
    DEMO_TAG_CODE,
    DEMO_ZEP_USER_ID,
    SYNTHETIC_TAG_CODE,
    validate_synthetic_patient,
)
from medtrace_agent.integrations.sponsor_error import SponsorIntegrationError
from medtrace_agent.medplum import MedplumError, medplum_configured
from medtrace_agent.medplum_repository import FACT_SYSTEM, repository


DISPLAY_NAME = "Jane Doe"
DATE_OF_BIRTH = "2004-04-04"


def _upsert_fact(patient_id: str, suffix: str, resource: dict[str, Any]) -> str:
    value = f"yc-demo:{suffix}"
    resource["identifier"] = [{"system": FACT_SYSTEM, "value": value}]
    saved = repository().client.conditional_upsert(
        resource,
        identifier=f"{FACT_SYSTEM}|{value}",
    )
    return f"{saved.get('resourceType')}/{saved.get('id')}"


def _seed_clinical_history(patient_id: str) -> list[str]:
    patient = {"reference": f"Patient/{patient_id}"}
    resources = [
        (
            "condition-diabetes",
            {
                "resourceType": "Condition",
                "clinicalStatus": {
                    "coding": [
                        {
                            "system": "http://terminology.hl7.org/CodeSystem/condition-clinical",
                            "code": "active",
                        }
                    ]
                },
                "verificationStatus": {
                    "coding": [
                        {
                            "system": "http://terminology.hl7.org/CodeSystem/condition-ver-status",
                            "code": "confirmed",
                        }
                    ]
                },
                "code": {"text": "Type 2 diabetes"},
                "subject": patient,
                "recordedDate": "2025-08-12",
                "note": [{"text": "Verified synthetic demo fixture; not real clinical data."}],
            },
        ),
        (
            "medication-metformin",
            {
                "resourceType": "MedicationStatement",
                "status": "active",
                "medicationCodeableConcept": {"text": "Metformin"},
                "subject": patient,
                "effectivePeriod": {"start": "2025-08-12"},
                "dateAsserted": "2026-07-15",
                "dosage": [{"text": "1000 mg twice daily"}],
                "note": [{"text": "Verified synthetic demo fixture; not real clinical data."}],
            },
        ),
        (
            "allergy-penicillin",
            {
                "resourceType": "AllergyIntolerance",
                "clinicalStatus": {
                    "coding": [
                        {
                            "system": "http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical",
                            "code": "active",
                        }
                    ]
                },
                "verificationStatus": {
                    "coding": [
                        {
                            "system": "http://terminology.hl7.org/CodeSystem/allergyintolerance-verification",
                            "code": "confirmed",
                        }
                    ]
                },
                "code": {"text": "Penicillin"},
                "patient": patient,
                "recordedDate": "2025-08-12",
                "reaction": [{"manifestation": [{"text": "Rash"}]}],
                "note": [{"text": "Verified synthetic demo fixture; not real clinical data."}],
            },
        ),
        (
            "hba1c-2026-01",
            {
                "resourceType": "Observation",
                "status": "final",
                "code": {"text": "HbA1c"},
                "subject": patient,
                "effectiveDateTime": "2026-01-15T09:00:00Z",
                "valueQuantity": {"value": 7.2, "unit": "%"},
                "interpretation": [{"text": "High"}],
                "referenceRange": [{"text": "< 7.0%"}],
                "note": [{"text": "Verified synthetic demo fixture; not real clinical data."}],
            },
        ),
        (
            "hba1c-2026-07",
            {
                "resourceType": "Observation",
                "status": "final",
                "code": {"text": "HbA1c"},
                "subject": patient,
                "effectiveDateTime": "2026-07-15T09:00:00Z",
                "valueQuantity": {"value": 8.1, "unit": "%"},
                "interpretation": [{"text": "High"}],
                "referenceRange": [{"text": "< 7.0%"}],
                "note": [{"text": "Verified synthetic demo fixture; not real clinical data."}],
            },
        ),
        (
            "encounter-diabetes-follow-up",
            {
                "resourceType": "Encounter",
                "status": "finished",
                "class": {
                    "system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
                    "code": "AMB",
                    "display": "ambulatory",
                },
                "type": [{"text": "Diabetes follow-up and medication-adherence review"}],
                "subject": patient,
                "period": {
                    "start": "2026-07-15T09:00:00Z",
                    "end": "2026-07-15T09:30:00Z",
                },
                "reasonCode": [{"text": "Worsening HbA1c"}],
            },
        ),
    ]
    return [_upsert_fact(patient_id, suffix, resource) for suffix, resource in resources]


def main() -> int:
    load_repo_env()
    if not medplum_configured():
        print(
            "Configure MEDPLUM_BASE_URL, MEDPLUM_CLIENT_ID, and MEDPLUM_CLIENT_SECRET first."
        )
        return 2
    try:
        repo = repository()
        patient = repo.upsert_patient(
            zep_user_id=DEMO_ZEP_USER_ID,
            display_name=DISPLAY_NAME,
            dob=DATE_OF_BIRTH,
            sex="F",
            primary_doctor="Dr. Smith (Primary care)",
            tags=[SYNTHETIC_TAG_CODE, DEMO_TAG_CODE, DEMO_STEDI_TAG_CODE],
        )
        patient_id = str(patient.get("id") or "")
        configured = (os.environ.get("YC_DEMO_PATIENT_ID") or "").strip()
        if configured and configured != patient_id:
            print(
                "YC_DEMO_PATIENT_ID points to a different Patient. Clear or correct it before provisioning."
            )
            return 3
        os.environ["YC_DEMO_PATIENT_ID"] = patient_id
        validate_synthetic_patient(patient, patient_id)
        resources = _seed_clinical_history(patient_id)
    except (MedplumError, SponsorIntegrationError) as exc:
        print(f"Medplum provisioning failed: {exc}")
        return 4

    print("Synthetic Medplum demo Patient and longitudinal chart reconciled.")
    print(f"YC_DEMO_PATIENT_ID={patient_id}")
    print(f"FHIR_RESOURCES={len(resources)}")
    print("Zep remains a retryable projection handled by npm run dev:zep-sync.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
