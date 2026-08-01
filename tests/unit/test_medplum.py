from __future__ import annotations

import base64
import json
from types import SimpleNamespace

import httpx
import pytest

from medtrace_agent.medplum import MedplumClient, MedplumError
from medtrace_agent.medplum_extraction import facts_from_plain_text, facts_from_vlm_pages
from medtrace_agent.medplum_repository import (
    AI_UNVERIFIED_TAG,
    FACT_SYSTEM,
    MedplumRepository,
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
