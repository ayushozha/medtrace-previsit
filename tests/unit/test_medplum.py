from __future__ import annotations

import base64
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from apps.api.routers import medplum_threads
from medtrace_agent.medplum import MedplumClient, MedplumError
from medtrace_agent.medplum_extraction import facts_from_plain_text, facts_from_vlm_pages
from apps.api.routers.medplum_common import is_synthetic_patient
from medtrace_agent.medplum_repository import (
    AI_UNVERIFIED_TAG,
    CHECKLIST_SYSTEM,
    CONSULTATION_SYSTEM,
    FACT_SYSTEM,
    MedplumRepository,
    TAG_SYSTEM,
    ZEP_USER_SYSTEM,
    binary_id_from_url,
)


def response(status: int, data: dict, url: str = "http://localhost:8103/fhir/R4/Patient") -> httpx.Response:
    return httpx.Response(status, json=data, request=httpx.Request("GET", url))


def test_client_caches_token_and_reads_paginated_search(monkeypatch: pytest.MonkeyPatch) -> None:
    auth_calls = 0
    request_calls = 0

    def fake_post(*args, **kwargs):
        nonlocal auth_calls
        auth_calls += 1
        return response(200, {"access_token": "secret-token", "expires_in": 3600}, "http://localhost:8103/oauth2/token")

    def fake_request(method, url, **kwargs):
        nonlocal request_calls
        request_calls += 1
        assert kwargs["headers"]["Authorization"] == "Bearer secret-token"
        if request_calls == 1:
            return response(200, {
                "resourceType": "Bundle",
                "type": "searchset",
                "entry": [{"resource": {"resourceType": "Patient", "id": "one"}}],
                "link": [{"relation": "next", "url": "http://localhost:8103/fhir/R4/Patient?_page=2"}],
            }, url)
        return response(200, {
            "resourceType": "Bundle",
            "type": "searchset",
            "entry": [{"resource": {"resourceType": "Patient", "id": "two"}}],
        }, url)

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(httpx, "request", fake_request)
    client = MedplumClient(client_id="client", client_secret="secret")
    assert [row["id"] for row in client.search("Patient")] == ["one", "two"]
    assert auth_calls == 1


def test_client_reauthenticates_once_on_401(monkeypatch: pytest.MonkeyPatch) -> None:
    tokens = iter(("first", "second"))
    calls = 0

    monkeypatch.setattr(
        httpx,
        "post",
        lambda *args, **kwargs: response(200, {"access_token": next(tokens), "expires_in": 3600}, "http://localhost:8103/oauth2/token"),
    )

    def fake_request(method, url, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return response(401, {"resourceType": "OperationOutcome", "issue": [{"diagnostics": "expired"}]}, url)
        assert kwargs["headers"]["Authorization"] == "Bearer second"
        return response(200, {"resourceType": "Patient", "id": "p1"}, url)

    monkeypatch.setattr(httpx, "request", fake_request)
    assert MedplumClient(client_id="client", client_secret="secret").read("Patient", "p1")["id"] == "p1"
    assert calls == 2


def test_operation_outcome_is_sanitized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *args, **kwargs: response(200, {"access_token": "token", "expires_in": 3600}, "http://localhost:8103/oauth2/token"),
    )
    monkeypatch.setattr(
        httpx,
        "request",
        lambda *args, **kwargs: response(
            422,
            {"resourceType": "OperationOutcome", "issue": [{"severity": "error", "diagnostics": "Patient.identifier is invalid"}]},
        ),
    )
    with pytest.raises(MedplumError, match="Patient.identifier is invalid") as caught:
        MedplumClient(client_id="client", client_secret="secret").create({"resourceType": "Patient"})
    assert caught.value.status_code == 422


def test_update_uses_version_if_match_and_surfaces_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *args, **kwargs: response(
            200,
            {"access_token": "token", "expires_in": 3600},
            "http://localhost:8103/oauth2/token",
        ),
    )
    captured: dict[str, str] = {}

    def fake_request(method, url, **kwargs):
        captured.update(kwargs["headers"])
        return response(
            412,
            {
                "resourceType": "OperationOutcome",
                "issue": [{"severity": "error", "diagnostics": "Version conflict"}],
            },
            url,
        )

    monkeypatch.setattr(httpx, "request", fake_request)
    client = MedplumClient(client_id="client", client_secret="secret")
    with pytest.raises(MedplumError, match="Version conflict") as caught:
        client.update(
            {
                "resourceType": "DocumentReference",
                "id": "journal-1",
                "meta": {"versionId": "7"},
            }
        )
    assert captured["If-Match"] == 'W/"7"'
    assert caught.value.status_code == 412


def test_binary_upload_sets_patient_security_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *args, **kwargs: response(
            200,
            {"access_token": "token", "expires_in": 3600},
            "http://localhost:8103/oauth2/token",
        ),
    )
    captured: dict[str, str] = {}

    def fake_request(method, url, **kwargs):
        captured.update(kwargs["headers"])
        return response(201, {"resourceType": "Binary", "id": "b1"}, url)

    monkeypatch.setattr(httpx, "request", fake_request)
    result = MedplumClient(client_id="client", client_secret="secret").create_binary(
        b"synthetic",
        content_type="text/plain",
        security_context="Patient/p1",
    )
    assert result["id"] == "b1"
    assert captured["X-Security-Context"] == "Patient/p1"


def test_binary_id_parses_fhir_and_self_hosted_storage_urls() -> None:
    assert binary_id_from_url("Binary/binary-1") == "binary-1"
    assert (
        binary_id_from_url("http://localhost:8103/storage/binary-2/object-key?Signature=test")
        == "binary-2"
    )


def test_client_rejects_external_pagination_link(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *args, **kwargs: response(200, {"access_token": "token", "expires_in": 3600}, "http://localhost:8103/oauth2/token"),
    )
    monkeypatch.setattr(
        httpx,
        "request",
        lambda *args, **kwargs: response(200, {
            "resourceType": "Bundle",
            "entry": [],
            "link": [{"relation": "next", "url": "https://example.com/steal"}],
        }),
    )
    with pytest.raises(MedplumError, match="outside"):
        MedplumClient(client_id="client", client_secret="secret").search("Patient")


def test_plain_text_extraction_is_conservative() -> None:
    facts = facts_from_plain_text("HbA1c: 8.4%\nNarrative says the patient may feel tired.")
    assert facts.conditions == []
    assert facts.medications == []
    assert facts.observations[0].name == "HbA1c"
    assert facts.observations[0].value == "8.4"


def test_unauthenticated_demo_boundary_accepts_only_synthetic_patients() -> None:
    assert is_synthetic_patient(
        {"meta": {"tag": [{"system": TAG_SYSTEM, "code": "synthetic"}]}}
    )
    assert not is_synthetic_patient({"meta": {"tag": []}})
    assert not is_synthetic_patient(None)


def test_vlm_pages_convert_to_typed_facts() -> None:
    page = SimpleNamespace(
        page_number=2,
        diagnoses_or_impressions=["Type 2 diabetes"],
        medications=["Metformin 500 mg twice daily"],
        allergies=["Penicillin"],
        labs=[SimpleNamespace(name="HbA1c", value="8.4", unit="%", ref_range="< 7%", flag="high")],
    )
    facts = facts_from_vlm_pages([page])
    assert facts.conditions[0].source_page == 2
    assert facts.medications[0].name == "Metformin"
    assert facts.allergies[0].substance == "Penicillin"
    assert facts.observations[0].unit == "%"


class FakeClient:
    def __init__(self) -> None:
        self.transaction_entries: list[dict] | None = None

    def transaction(self, entries: list[dict]) -> dict:
        self.transaction_entries = entries
        return {"resourceType": "Bundle", "entry": []}


def test_extracted_fact_bundle_is_idempotent_and_unverified() -> None:
    fake = FakeClient()
    repo = MedplumRepository(fake)  # type: ignore[arg-type]
    facts = facts_from_plain_text("Glucose 140 mg/dL")
    repo.create_extracted_facts(patient_id="p1", doc_id="d1", facts=facts, model_name="synthetic-test")
    assert fake.transaction_entries is not None
    observation = fake.transaction_entries[0]["resource"]
    assert observation["status"] == "preliminary"
    assert observation["identifier"][0]["system"] == FACT_SYSTEM
    assert observation["meta"]["tag"] == [AI_UNVERIFIED_TAG]
    assert fake.transaction_entries[0]["request"]["method"] == "PUT"
    provenance = fake.transaction_entries[-1]["resource"]
    assert provenance["resourceType"] == "Provenance"
    assert fake.transaction_entries[-1]["request"]["method"] == "PUT"
    assert "_tag=" in fake.transaction_entries[-1]["request"]["url"]
    assert {target["reference"] for target in provenance["target"]} >= {"Patient/p1", "DocumentReference/d1"}


def test_patient_view_uses_stable_zep_identifier() -> None:
    repo = MedplumRepository(FakeClient())  # type: ignore[arg-type]
    view = repo.patient_view({
        "resourceType": "Patient",
        "id": "p1",
        "identifier": [{"system": ZEP_USER_SYSTEM, "value": "zep-1"}],
        "name": [{"text": "Synthetic Person"}],
        "gender": "female",
        "birthDate": "2000-01-01",
    })
    assert view["id"] == "p1"
    assert view["zep_user_id"] == "zep-1"
    assert view["sex"] == "F"


def test_checklist_upsert_rejects_an_item_owned_by_another_patient() -> None:
    class ChecklistClient:
        def search_one(self, resource_type, params):
            assert resource_type == "Task"
            assert params == {"identifier": f"{CHECKLIST_SYSTEM}|item-1"}
            return {"resourceType": "Task", "for": {"reference": "Patient/patient-a"}}

        def conditional_upsert(self, resource, *, identifier):  # pragma: no cover
            raise AssertionError("A cross-patient Task must not be written")

    repo = MedplumRepository(ChecklistClient())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="different patient"):
        repo.upsert_checklist_item(
            patient_id="patient-b",
            item_id="item-1",
            text="Verify medication",
            done=False,
        )


def test_consultation_identifiers_are_patient_scoped() -> None:
    class ConsultationClient:
        def __init__(self):
            self.identifiers = []

        def read(self, resource_type, resource_id):
            return {"resourceType": resource_type, "id": resource_id, "name": [{"text": "Patient"}]}

        def transaction(self, entries):
            self.identifiers.extend(entry["request"]["url"] for entry in entries)
            return {
                "entry": [
                    {"response": {"location": f"{entry['resource']['resourceType']}/resource-{index}/_history/1"}}
                    for index, entry in enumerate(entries)
                ]
            }

    client = ConsultationClient()
    repo = MedplumRepository(client)  # type: ignore[arg-type]

    repo.upsert_consultation(
        patient_id="patient-a",
        consultation_id="session-1",
        transcript="Transcript",
        report="Report",
    )
    patient_a_identifiers = set(client.identifiers)
    client.identifiers.clear()
    repo.upsert_consultation(
        patient_id="patient-b",
        consultation_id="session-1",
        transcript="Transcript",
        report="Report",
    )

    assert any("patient-a" in identifier for identifier in patient_a_identifiers)
    assert any("patient-b" in identifier for identifier in client.identifiers)
    assert patient_a_identifiers.isdisjoint(client.identifiers)


def test_message_request_lookup_never_crosses_threads() -> None:
    class MessageClient:
        def search_one(self, resource_type, params):
            assert resource_type == "Communication"
            identifier = params["identifier"]
            if identifier.endswith("|thread-b:request-1:user"):
                return None
            if identifier.endswith("|request-1:user"):
                return {
                    "resourceType": "Communication",
                    "id": "message-a",
                    "partOf": [{"reference": "Communication/thread-a"}],
                }
            return None

    repo = MedplumRepository(MessageClient())  # type: ignore[arg-type]

    assert repo.message_by_request("thread-b", "request-1", "user") is None
    assert repo.message_by_request("thread-a", "request-1", "user") == {
        "resourceType": "Communication",
        "id": "message-a",
        "partOf": [{"reference": "Communication/thread-a"}],
    }


def test_message_history_rejects_a_non_synthetic_thread_subject(monkeypatch: pytest.MonkeyPatch) -> None:
    class Repo:
        def thread_by_zep(self, _thread_id):
            return {"id": "thread-resource", "subject": {"reference": "Patient/real-patient"}}

        def get_patient(self, _patient_id):
            return {"resourceType": "Patient", "id": "real-patient", "meta": {"tag": []}}

        def list_messages(self, *_args, **_kwargs):  # pragma: no cover
            raise AssertionError("Messages must not be read across the synthetic boundary")

    monkeypatch.setattr(medplum_threads, "repository", lambda: Repo())
    with pytest.raises(HTTPException) as exc_info:
        medplum_threads.list_messages("external-thread")
    assert exc_info.value.status_code == 404


def test_consultation_history_reconstructs_multiple_canonical_sessions() -> None:
    class BinaryClient:
        def read_binary(self, binary_id):
            return {
                "transcript-1": b"First transcript",
                "report-1": b"First report",
                "transcript-2": b"Second transcript",
                "report-2": b"Second report",
            }[binary_id]

    repo = MedplumRepository(BinaryClient())  # type: ignore[arg-type]
    repo.clinical_resources = lambda _patient_id: {  # type: ignore[method-assign]
        "Encounter": [
            {
                "resourceType": "Encounter",
                "id": "enc-1",
                "identifier": [{"system": CONSULTATION_SYSTEM, "value": "patient-a:session-1"}],
                "period": {"end": "2026-08-01T10:00:00Z"},
                "length": {"value": 65},
            },
            {
                "resourceType": "Encounter",
                "id": "enc-2",
                "identifier": [{"system": CONSULTATION_SYSTEM, "value": "patient-a:session-2"}],
                "period": {"end": "2026-08-01T11:00:00Z"},
            },
        ],
        "DocumentReference": [
            {
                "meta": {"tag": [{"system": TAG_SYSTEM, "code": "consultation"}, {"system": TAG_SYSTEM, "code": artifact}]},
                "context": {"encounter": [{"reference": f"Encounter/enc-{index}"}]},
                "content": [{"attachment": {"url": f"Binary/{artifact}-{index}", "contentType": "text/plain"}}],
            }
            for index in (1, 2)
            for artifact in ("transcript", "report")
        ],
    }

    rows = repo.consultation_views("patient-a")

    assert [row["id"] for row in rows] == ["session-2", "session-1"]
    assert rows[0]["transcript"] == "Second transcript"
    assert rows[1]["duration"] == "1:05"


def test_list_documents_excludes_internal_workflow_journals() -> None:
    resources = {
        "Task": [],
        "DocumentReference": [
            {
                "resourceType": "DocumentReference",
                "id": "clinical-1",
                "type": {"text": "clinical_pdf"},
                "date": "2026-08-01T10:00:00Z",
                "content": [{"attachment": {"title": "note.pdf", "url": "Binary/one"}}],
            },
            {
                "resourceType": "DocumentReference",
                "id": "eligibility-1",
                "type": {"text": "Stedi test-mode eligibility response"},
                "date": "2026-08-01T11:00:00Z",
                "content": [{"attachment": {"contentType": "application/json", "data": "e30="}}],
            },
            {
                "resourceType": "DocumentReference",
                "id": "reconstruction-1",
                "type": {"text": "Clinician-approved pre-visit reconstruction"},
                "date": "2026-08-01T12:00:00Z",
                "content": [{"attachment": {"contentType": "application/json", "data": "e30="}}],
            },
        ],
    }

    docs = MedplumRepository(FakeClient()).list_documents("patient-1", resources)  # type: ignore[arg-type]

    assert [doc["doc_id"] for doc in docs] == ["clinical-1"]


def test_clinical_views_collapse_reconfirmed_active_facts() -> None:
    resources = {
        "MedicationStatement": [
            {
                "id": "med-seeded",
                "status": "active",
                "meta": {"lastUpdated": "2026-01-01T00:00:00Z"},
                "medicationCodeableConcept": {"text": "Metformin"},
                "dosage": [{"text": "1000 mg twice daily"}],
            },
            {
                "id": "med-confirmed",
                "status": "active",
                "meta": {"lastUpdated": "2026-08-01T00:00:00Z"},
                "medicationCodeableConcept": {"text": "metformin"},
            },
            {
                "id": "med-error",
                "status": "entered-in-error",
                "meta": {"lastUpdated": "2026-08-02T00:00:00Z"},
                "medicationCodeableConcept": {"text": "Metformin"},
                "dosage": [{"text": "9999 mg hourly"}],
            },
            {
                "id": "med-unverified",
                "status": "active",
                "meta": {
                    "lastUpdated": "2026-08-03T00:00:00Z",
                    "tag": [AI_UNVERIFIED_TAG],
                },
                "medicationCodeableConcept": {"text": "Metformin"},
                "dosage": [{"text": "7777 mg hourly"}],
            },
            {
                "id": "med-previous-1",
                "status": "completed",
                "effectivePeriod": {"start": "2024-01-01", "end": "2024-01-10"},
                "medicationCodeableConcept": {"text": "Amoxicillin"},
                "dosage": [{"text": "500 mg twice daily"}],
            },
            {
                "id": "med-previous-2",
                "status": "completed",
                "effectivePeriod": {"start": "2025-02-01", "end": "2025-02-10"},
                "medicationCodeableConcept": {"text": "Amoxicillin"},
                "dosage": [{"text": "875 mg twice daily"}],
            },
        ],
        "AllergyIntolerance": [
            {
                "id": "allergy-seeded",
                "meta": {"lastUpdated": "2026-01-01T00:00:00Z"},
                "verificationStatus": {"text": "confirmed"},
                "code": {"text": "Penicillin"},
                "reaction": [{"manifestation": [{"text": "Widespread itchy rash"}]}],
            },
            {
                "id": "allergy-confirmed",
                "meta": {"lastUpdated": "2026-08-01T00:00:00Z"},
                "verificationStatus": {"text": "confirmed"},
                "code": {"text": "penicillin"},
                "reaction": [{"manifestation": [{"text": "Rash"}]}],
            },
            {
                "id": "allergy-error",
                "meta": {"lastUpdated": "2026-08-02T00:00:00Z"},
                "verificationStatus": {"text": "entered-in-error"},
                "code": {"text": "Penicillin"},
                "reaction": [{"manifestation": [{"text": "Anaphylaxis"}]}],
            },
            {
                "id": "allergy-unverified",
                "meta": {
                    "lastUpdated": "2026-08-03T00:00:00Z",
                    "tag": [AI_UNVERIFIED_TAG],
                },
                "verificationStatus": {"text": "unconfirmed"},
                "code": {"text": "Penicillin"},
                "reaction": [{"manifestation": [{"text": "Airway closure"}]}],
            },
            {
                "id": "allergy-old-confirmed",
                "meta": {"lastUpdated": "2026-01-01T00:00:00Z"},
                "verificationStatus": {"text": "confirmed"},
                "code": {"text": "Sulfonamide"},
                "reaction": [{"manifestation": [{"text": "Hives"}]}],
            },
            {
                "id": "allergy-new-refuted",
                "meta": {"lastUpdated": "2026-08-01T00:00:00Z"},
                "verificationStatus": {"text": "refuted"},
                "code": {"text": "Sulfonamide"},
            },
        ],
        "Provenance": [],
    }
    repo = MedplumRepository(FakeClient())  # type: ignore[arg-type]

    medications = repo.medication_views(resources)
    allergies = repo.allergy_views(resources)
    resources["MedicationStatement"].reverse()
    resources["AllergyIntolerance"].reverse()

    assert [(item["name"], item["dose"], item["frequency"], item["status"]) for item in medications] == [
        ("Metformin", "1000 mg", "twice daily", "Active"),
        ("Amoxicillin", "500 mg", "twice daily", "Previous"),
        ("Amoxicillin", "875 mg", "twice daily", "Previous"),
    ]
    assert [(item["allergen"], item["reaction"]) for item in allergies] == [
        ("Penicillin", "Rash")
    ]
    assert repo.medication_views(resources) == medications
    assert repo.allergy_views(resources) == allergies


def test_diagnostic_report_regeneration_reuses_its_binary() -> None:
    class ReportClient:
        def __init__(self) -> None:
            self.binary_updates: list[str] = []

        def search_one(self, resource_type, params):
            if resource_type == "ImagingStudy":
                return {"resourceType": "ImagingStudy", "id": "study-resource"}
            return {
                "resourceType": "DiagnosticReport",
                "id": "report-1",
                "presentedForm": [{"url": "Binary/report-binary"}],
            }

        def update_binary(self, binary_id, data, **kwargs):
            self.binary_updates.append(binary_id)
            return {"resourceType": "Binary", "id": binary_id}

        def create_binary(self, *args, **kwargs):  # pragma: no cover
            raise AssertionError("Regeneration must not orphan a new Binary")

        def update(self, resource):
            return resource

    client = ReportClient()
    repo = MedplumRepository(client)  # type: ignore[arg-type]
    result = repo.upsert_diagnostic_report(
        patient_id="patient-a",
        study_id="study-a",
        report={"summary": "Updated", "source": "http"},
    )

    assert client.binary_updates == ["report-binary"]
    assert result["presentedForm"][0]["url"] == "Binary/report-binary"


def test_repository_decodes_approved_reconstruction_context() -> None:
    payload = {"workflow_state": "complete", "draft": {"summary": "Missed metformin doses"}}
    resources = {
        "DocumentReference": [
            {
                "resourceType": "DocumentReference",
                "id": "journal-1",
                "date": "2026-08-01T12:00:00Z",
                "type": {"text": "Clinician-approved pre-visit reconstruction"},
                "content": [
                    {
                        "attachment": {
                            "contentType": "application/json",
                            "data": base64.b64encode(json.dumps(payload).encode()).decode(),
                        }
                    }
                ],
            }
        ]
    }
    rows = MedplumRepository(FakeClient()).json_document_payloads(  # type: ignore[arg-type]
        resources,
        document_type="Clinician-approved pre-visit reconstruction",
    )
    assert rows == [
        {
            "document_id": "journal-1",
            "date": "2026-08-01T12:00:00Z",
            "payload": payload,
        }
    ]


def test_needs_correction_restores_unverified_report_tag() -> None:
    class ReviewClient:
        def __init__(self) -> None:
            self.report = {
                "resourceType": "DiagnosticReport",
                "id": "report-1",
                "status": "preliminary",
                "meta": {"tag": [AI_UNVERIFIED_TAG]},
                "extension": [],
            }

        def search_one(self, resource_type, params):
            assert resource_type == "DiagnosticReport"
            return self.report

        def update(self, resource):
            self.report = resource
            return resource

        def conditional_upsert(self, resource, *, identifier):
            return {**resource, "id": "task-1"}

    client = ReviewClient()
    repo = MedplumRepository(client)  # type: ignore[arg-type]
    accepted, _ = repo.review_diagnostic_report(
        patient_id="patient-1",
        study_id="study-1",
        decision="accepted",
        reviewer_id="operator-1",
        reviewer_name="Dr. Reviewer",
    )
    assert AI_UNVERIFIED_TAG not in accepted["meta"]["tag"]
    assert repo.report_reviewer(accepted)["reviewer_id"] == "operator-1"

    corrected, _ = repo.review_diagnostic_report(
        patient_id="patient-1",
        study_id="study-1",
        decision="needs-correction",
        reviewer_id="operator-1",
        reviewer_name="Dr. Reviewer",
    )
    assert corrected["status"] == "preliminary"
    assert corrected["meta"]["tag"] == [AI_UNVERIFIED_TAG]
