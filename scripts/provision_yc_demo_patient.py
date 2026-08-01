"""Provision the cross-provider synthetic Jane Doe chart used by the YC demo.

This script writes only synthetic data. It uses real InsForge, Zep, and Medplum
credentials from the repo ``.env`` and prints the IDs that must be copied back to
``YC_DEMO_PATIENT_ID`` and ``MEDPLUM_PATIENT_ID``.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Any

from medtrace_agent.env import load_repo_env
from medtrace_agent.ingest.documents import ingest_plain_text_note_to_patient_graph
from medtrace_agent.insforge_api import (
    ensure_chart_subject_id,
    remote_insforge_configured,
    update_chart_subject_metadata,
)
from medtrace_agent.integrations.medplum import (
    CHART_IDENTIFIER_SYSTEM,
    SYNTHETIC_TAG_CODE,
    SYNTHETIC_TAG_SYSTEM,
    MedplumClient,
)
from medtrace_agent.integrations.stedi import TEST_CASE_ID
from medtrace_agent.local_store import chronic_care_demo_fixture, local_mock_enabled
from medtrace_agent.zep.graph import list_recent_episodes
from medtrace_agent.zep.memory import ensure_user

DISPLAY_NAME = "Jane Doe"
DATE_OF_BIRTH = "2004-04-04"
ZEP_USER_ID = "yc-medplum-demo-jane-doe"
HISTORY_DOC_ID = "yc-medplum-demo-jane-doe-history-v1"
HISTORY_NOTE = """# Synthetic longitudinal chart history

Jane Doe has type 2 diabetes managed with metformin. HbA1c worsened from 7.2% to
8.1% over the latest longitudinal measurements. Medication reconciliation lists
metformin 1000 mg twice daily. The allergy list records penicillin with a rash
reaction. The existing timeline includes diabetes follow-up, repeat HbA1c, and
medication-adherence review. This is synthetic hackathon data, not a real patient.
"""


def _chart_metadata(medplum_patient_id: str | None = None) -> dict[str, Any]:
    fixture = chronic_care_demo_fixture(DISPLAY_NAME)
    metadata = {
        "fields": {
            "age": 22,
            "sex": "F",
            "dob": DATE_OF_BIRTH,
            "primary_doctor": "Dr. Smith (Primary care)",
            "last_visit": fixture.get("last_visit"),
            "notes": "Synthetic YC Medplum and Stedi test persona.",
            "tags": ["synthetic", "yc-medplum-demo", TEST_CASE_ID],
        },
        "summary": fixture.get("summary"),
        "risk_level": fixture.get("risk_level") or "Medium",
        "condition_count": fixture.get("condition_count") or 1,
        "doctor_checklist": fixture.get("doctor_checklist") or [],
        "insights": fixture.get("insights") or [],
        "clinical": fixture.get("clinical") or {},
        "created_via": "yc_demo_provisioner",
    }
    if medplum_patient_id:
        metadata["yc_medplum_demo"] = {
            "synthetic": True,
            "medplum_patient_id": medplum_patient_id,
            "stedi_test_case": TEST_CASE_ID,
        }
    return metadata


async def _resolve_medplum_patient(
    client: MedplumClient,
    chart_id: str,
    *,
    create_patient: bool,
) -> str:
    configured = (os.environ.get("MEDPLUM_PATIENT_ID") or "").strip()
    if configured:
        await client.assert_synthetic_patient(chart_id)
        return configured
    if not create_patient:
        raise RuntimeError(
            "MEDPLUM_PATIENT_ID is unset. Re-run with --create-medplum-patient to create the "
            "explicitly tagged synthetic Patient."
        )
    resource = {
        "resourceType": "Patient",
        "meta": {
            "tag": [{"system": SYNTHETIC_TAG_SYSTEM, "code": SYNTHETIC_TAG_CODE}]
        },
        "identifier": [{"system": CHART_IDENTIFIER_SYSTEM, "value": chart_id}],
        "active": True,
        "name": [{"use": "official", "family": "Doe", "given": ["Jane"]}],
        "gender": "female",
        "birthDate": DATE_OF_BIRTH,
    }
    created = await client.create_validated(resource)
    patient_id = str(created.get("resource_id") or "")
    if not patient_id:
        raise RuntimeError("Medplum created no Patient id.")
    os.environ["MEDPLUM_PATIENT_ID"] = patient_id
    await client.assert_synthetic_patient(chart_id)
    return patient_id


def _ensure_history() -> list[str]:
    marker = f"doc_id={HISTORY_DOC_ID} "
    existing = [
        str(row.get("uuid"))
        for row in list_recent_episodes(ZEP_USER_ID, lastn=100, truncate_chars=None)
        if row.get("uuid") and marker in str(row.get("content") or "")
    ]
    if existing:
        return existing
    return ingest_plain_text_note_to_patient_graph(
        ZEP_USER_ID,
        HISTORY_NOTE,
        note_source="session_note",
        filename="synthetic-jane-doe-history.txt",
        doc_id=HISTORY_DOC_ID,
        extra_metadata={"synthetic": True, "yc_demo_seed": True},
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--create-medplum-patient",
        action="store_true",
        help="Create the tagged synthetic Medplum Patient when MEDPLUM_PATIENT_ID is unset.",
    )
    args = parser.parse_args()
    load_repo_env()
    if local_mock_enabled() or not remote_insforge_configured():
        print("Real InsForge must be configured and MEDTRACE_LOCAL_MOCK must be disabled.")
        return 2
    if not (os.environ.get("ZEP_API_KEY") or "").strip():
        print("ZEP_API_KEY is required to provision the synthetic history.")
        return 2

    ensure_user(ZEP_USER_ID, DISPLAY_NAME)
    configured_medplum_id = (os.environ.get("MEDPLUM_PATIENT_ID") or "").strip() or None
    chart_id = ensure_chart_subject_id(
        zep_user_id=ZEP_USER_ID,
        display_name=DISPLAY_NAME,
        metadata=_chart_metadata(configured_medplum_id),
    )
    if not chart_id:
        print("InsForge did not return a chart_subject id.")
        return 3

    try:
        medplum_id = asyncio.run(
            _resolve_medplum_patient(
                MedplumClient(), chart_id, create_patient=args.create_medplum_patient
            )
        )
    except Exception as exc:
        print(f"Medplum provisioning failed: {exc}")
        return 4

    updated = update_chart_subject_metadata(
        chart_subject_id=chart_id,
        metadata_patch=_chart_metadata(medplum_id),
    )
    if not updated:
        print("InsForge chart binding update failed.")
        return 5
    episode_ids = _ensure_history()
    print("Synthetic demo patient provisioned and cross-provider binding verified.")
    print(f"YC_DEMO_PATIENT_ID={chart_id}")
    print(f"MEDPLUM_PATIENT_ID={medplum_id}")
    print(f"ZEP_USER_ID={ZEP_USER_ID}")
    print(f"ZEP_EPISODES={len(episode_ids)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
