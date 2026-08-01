from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from apps.api.routers import demo
from apps.api.schemas import DemoConfirmIn
from medtrace_agent.agents.previsit import PrevisitDraft, ProposedChange, validate_evidence
from medtrace_agent.integrations import deepgram, medplum, moss_retrieval, stedi
from medtrace_agent.integrations.sponsor_error import SponsorIntegrationError
from scripts import medplum_zep_worker, provision_yc_demo_patient


def _utterance() -> dict[str, Any]:
    return {
        "id": "dg-1",
        "speaker": 1,
        "start": 1.2,
        "end": 4.4,
        "text": "I miss my metformin after overnight shifts.",
        "confidence": 0.98,
    }


def _draft() -> PrevisitDraft:
    return PrevisitDraft(
        summary="Medication adherence barrier reported.",
        proposed_changes=[
            ProposedChange(
                kind="medication_adherence",
                title="Metformin adherence",
                clinical_subject="metformin",
                proposed_value="Missed doses after overnight shifts.",
                evidence_utterance_id="dg-1",
                evidence_quote="miss my metformin after overnight shifts",
                clinician_note="Confirm schedule and frequency.",
            )
        ],
        unresolved_questions=["How many doses were missed this week?"],
        clinician_verification=["Confirm the number of missed doses."],
        recommended_visit=True,
        recommended_service="Primary care follow-up",
    )


def _confirm_payload(monkeypatch: pytest.MonkeyPatch, *, approved: bool = True) -> DemoConfirmIn:
    monkeypatch.setenv("YC_DEMO_CHECKIN_SIGNING_KEY", "test-signing-key-with-at-least-32-characters")
    monkeypatch.setenv("YC_DEMO_ACCESS_TOKEN", "different-test-access-token-with-32-characters")
    utterances = [_utterance()]
    source_draft = _draft().model_dump(mode="json")
    return DemoConfirmIn(
        checkin_id="checkin-1",
        deepgram_request_id="deepgram-1",
        openai_response_id="openai-1",
        checkin_token=demo._issue_checkin_token(  # noqa: SLF001 - verifies the trust-boundary contract
            checkin_id="checkin-1",
            patient_id="chart-1",
            deepgram_request_id="deepgram-1",
            openai_response_id="openai-1",
            patient_speaker=1,
            utterances=utterances,
            draft=source_draft,
        ),
        patient_speaker=1,
        utterances=utterances,
        source_draft=source_draft,
        draft=source_draft,
        approved=approved,
    )


def _operator() -> demo.DemoOperator:
    return demo.DemoOperator(operator_id="operator-1", display_name="Dr. Reviewer")


def test_demo_auth_precedes_chart_access(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YC_DEMO_ACCESS_TOKEN", "different-test-access-token-with-32-characters")
    monkeypatch.setenv("YC_DEMO_OPERATOR_ID", "operator-1")
    monkeypatch.setenv("YC_DEMO_OPERATOR_NAME", "Dr. Reviewer")
    data_layer_calls = 0

    class FailIfCalled:
        def __init__(self) -> None:
            nonlocal data_layer_calls
            data_layer_calls += 1
            raise AssertionError("Unauthenticated traffic reached Medplum")

    def fail_if_called() -> bool:
        nonlocal data_layer_calls
        data_layer_calls += 1
        raise AssertionError("Unauthenticated traffic reached the data layer")

    monkeypatch.setattr(demo, "MedplumClient", FailIfCalled)
    monkeypatch.setattr(demo, "repository", fail_if_called)
    test_app = FastAPI()
    test_app.include_router(demo.router)

    async def request(status_header: dict[str, str]) -> int:
        transport = httpx.ASGITransport(app=test_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/api/demo/patients/example/readiness",
                headers=status_header,
            )
            return response.status_code

    assert asyncio.run(request({})) == 401
    assert asyncio.run(request({"X-MedTrace-Demo-Token": "wrong-token"})) == 403
    assert data_layer_calls == 0


def test_evidence_validator_preserves_exact_deepgram_text() -> None:
    draft = validate_evidence(_draft(), [_utterance()])
    assert draft.proposed_changes[0].evidence_quote == "miss my metformin after overnight shifts"


def test_evidence_validator_rejects_an_unsupported_quote() -> None:
    draft = _draft()
    draft.proposed_changes[0].evidence_quote = "a quote that was never spoken"
    with pytest.raises(SponsorIntegrationError, match="not an exact Deepgram transcript quote"):
        validate_evidence(draft, [_utterance()])


def test_deepgram_word_fallback_groups_only_explicit_speakers() -> None:
    result = deepgram._words_to_utterances(  # noqa: SLF001 - focused parser regression test
        [
            {"speaker": 0, "word": "hello", "start": 0, "end": 0.4},
            {"speaker": 0, "punctuated_word": "there.", "start": 0.4, "end": 0.8},
            {"speaker": 1, "word": "thanks", "start": 0.9, "end": 1.2},
            {"word": "unlabelled", "start": 1.2, "end": 1.4},
        ]
    )
    assert [(item["speaker"], item["transcript"]) for item in result] == [
        (0, "hello there."),
        (1, "thanks"),
    ]


def test_stedi_test_mode_normalizes_payer_cost_sharing(monkeypatch: pytest.MonkeyPatch) -> None:
    env = {
        "STEDI_TEST_API_KEY": "test-key",
        "STEDI_TRADING_PARTNER_SERVICE_ID": "60054",
        "STEDI_PROVIDER_ORGANIZATION_NAME": "Provider Name",
        "STEDI_PROVIDER_NPI": "1999999984",
        "STEDI_SUBSCRIBER_MEMBER_ID": "AETNA12345",
        "STEDI_SUBSCRIBER_FIRST_NAME": "Jane",
        "STEDI_SUBSCRIBER_LAST_NAME": "Doe",
        "STEDI_SUBSCRIBER_DATE_OF_BIRTH": "20040404",
        "STEDI_SERVICE_TYPE_CODES": "30",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Key test-key"
        assert request.url.params.get("diarize") is None
        body = json.loads(request.content)
        assert body == {
            "controlNumber": body["controlNumber"],
            "tradingPartnerServiceId": "60054",
            "provider": {"organizationName": "Provider Name", "npi": "1999999984"},
            "subscriber": {
                "memberId": "AETNA12345",
                "firstName": "Jane",
                "lastName": "Doe",
                "dateOfBirth": "20040404",
            },
            "encounter": {"serviceTypeCodes": ["30"]},
        }
        return httpx.Response(
            200,
            json={
                "id": "stedi-transaction",
                "meta": {"applicationMode": "test", "traceId": "trace-1"},
                "planStatus": [{"statusCode": "1"}],
                "benefitsInformation": [
                    {
                        "code": "B",
                        "name": "Co-Payment",
                        "benefitAmount": 30,
                        "serviceTypeCodes": ["30"],
                        "inPlanNetworkIndicatorCode": "Y",
                        "coverageLevelCode": "IND",
                        "timeQualifierCode": "29",
                    },
                    {"code": "A", "name": "Co-Insurance", "benefitPercent": 0.2},
                ],
            },
        )

    real_async_client = httpx.AsyncClient

    def client_factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        return real_async_client(transport=httpx.MockTransport(handler), timeout=kwargs.get("timeout"))

    monkeypatch.setattr(stedi.httpx, "AsyncClient", client_factory)
    result = asyncio.run(stedi.check_eligibility())
    assert result["application_mode"] == "test"
    assert result["coverage_active"] is True
    assert result["benefits"][0]["benefit_amount"] == 30.0
    assert result["patient_responsibility_summary"].startswith("Not determinable")
    assert "Stedi-generated synthetic test data" in result["disclaimer"]
    assert "raw" not in result


def _install_journal_mocks(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {}

    def find(*_: Any, **__: Any) -> dict[str, Any] | None:
        return state.get("row")

    def update(
        patient_id: str, checkin_id: str, metadata_patch: dict[str, Any]
    ) -> dict[str, Any] | None:
        assert patient_id == "chart-1"
        row = state.get("row")
        if not row or row["metadata"].get("checkin_id") != checkin_id:
            return None
        row["metadata"].update(metadata_patch)
        return row

    monkeypatch.setattr(demo, "_find_checkin_document", find)
    monkeypatch.setattr(demo, "_update_document_metadata", update)
    return state


def test_confirm_validates_before_medplum_transaction(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YC_DEMO_PATIENT_ID", "chart-1")
    events: list[str] = []
    state = _install_journal_mocks(monkeypatch)

    class FakeMedplum:
        async def assert_synthetic_patient(self, chart_id: str) -> dict[str, Any]:
            events.append("patient")
            assert chart_id == "chart-1"
            return {"resourceType": "Patient"}

        async def validate_resources(self, resources: list[dict[str, Any]]) -> list[dict[str, Any]]:
            events.append("validate")
            return [
                {"resource_type": resource["resourceType"], "valid": True, "notices": []}
                for resource in resources
            ]

        async def transact(
            self, entries: list[dict[str, Any]], *, checkin_id: str
        ) -> list[dict[str, Any]]:
            events.append("transact")
            results = [
                {
                    "resource_type": entry["resource"]["resourceType"],
                    "resource_id": f"id-{index}",
                    "version_id": "1",
                    "location": None,
                    "status": "201",
                    "checkin_id": checkin_id,
                }
                for index, entry in enumerate(entries)
            ]
            journal_index = next(
                index
                for index, entry in enumerate(entries)
                if entry["resource"]["resourceType"] == "DocumentReference"
            )
            state["row"] = {
                "doc_id": f"id-{journal_index}",
                "metadata": demo._decode_document_json(  # noqa: SLF001
                    entries[journal_index]["resource"]
                ),
            }
            return results

    monkeypatch.setattr(demo, "MedplumClient", FakeMedplum)
    monkeypatch.setattr(demo, "_enforce_sponsor_rate_limit", lambda *_: None)

    payload = _confirm_payload(monkeypatch)
    result = asyncio.run(
        demo.confirm_checkin(
            "chart-1", payload, _operator(), {"zep_user_id": "synthetic-user"}
        )
    )
    assert events == ["patient", "validate", "transact"]
    assert result.validation_status == "passed"
    assert result.resources
    assert result.document_id.startswith("id-")


def test_confirm_retry_returns_saved_reconstruction_without_another_fhir_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _confirm_payload(monkeypatch)
    row = {
        "doc_id": payload.checkin_id,
        "metadata": {
            "draft": payload.draft.model_dump(mode="json"),
            "operator_id": _operator().operator_id,
            "review_audit": demo._review_audit(payload),  # noqa: SLF001
            "workflow_state": "complete",
            "validation_status": "passed",
            "validations": [{"resource_type": "Encounter", "valid": True, "notices": []}],
            "medplum_resources": [
                {
                    "resource_type": "Encounter",
                    "resource_id": "existing-encounter",
                    "version_id": "1",
                    "location": "Encounter/existing-encounter/_history/1",
                    "status": "200",
                }
            ],
        },
    }
    monkeypatch.setattr(demo, "_find_checkin_document", lambda *_, **__: row)
    monkeypatch.setattr(demo, "_enforce_sponsor_rate_limit", lambda *_: None)

    class FailIfCalled:
        def __init__(self) -> None:
            raise AssertionError("Medplum must not be called for an already-saved approval")

    monkeypatch.setattr(demo, "MedplumClient", FailIfCalled)
    result = asyncio.run(
        demo.confirm_checkin(
            "chart-1", payload, _operator(), {"zep_user_id": "synthetic-user"}
        )
    )
    assert result.resources[0].resource_id == "existing-encounter"


def test_rejected_confirmation_never_builds_fhir(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _confirm_payload(monkeypatch, approved=False)
    monkeypatch.setattr(demo, "_enforce_sponsor_rate_limit", lambda *_: None)
    with pytest.raises(HTTPException, match="No FHIR write occurs"):
        asyncio.run(
            demo.confirm_checkin(
                "chart-1", payload, _operator(), {"zep_user_id": "synthetic-user"}
            )
        )


def test_confirmation_rejects_tampered_deepgram_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _confirm_payload(monkeypatch)
    payload.utterances[0].text = "A browser-substituted transcript."
    with pytest.raises(SponsorIntegrationError, match="no longer matches"):
        demo._verify_checkin_token("chart-1", payload)  # noqa: SLF001


def test_confirmation_allows_and_audits_clinician_correction(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _confirm_payload(monkeypatch)
    payload.draft.proposed_changes[0].kind = "follow_up"
    payload.draft.proposed_changes[0].clinical_subject = "primary care follow-up"
    payload.draft.proposed_changes[0].proposed_value = "Review two missed doses."
    demo._verify_checkin_token("chart-1", payload)  # noqa: SLF001
    audit = demo._review_audit(payload)  # noqa: SLF001
    assert any(item["path"].endswith("kind") for item in audit["differences"])


def test_fhir_resources_use_clinical_subject_and_proposal_semantics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("YC_DEMO_PATIENT_ID", "patient-fhir-id")
    payload = _confirm_payload(monkeypatch)
    payload.draft.proposed_changes.extend(
        [
            ProposedChange(
                kind="allergy_confirmation",
                title="Penicillin allergy",
                clinical_subject="penicillin",
                proposed_value="Rash",
                evidence_utterance_id="dg-1",
                evidence_quote="miss my metformin",
                clinician_note="Confirm reaction severity.",
            ),
            ProposedChange(
                kind="follow_up",
                title="Primary care follow-up",
                clinical_subject="primary care visit",
                proposed_value="Review medication adherence barrier.",
                evidence_utterance_id="dg-1",
                evidence_quote="overnight shifts",
                clinician_note="Schedule only after clinician review.",
            ),
        ]
    )
    resources = [
        resource
        for _, resource in demo._fhir_resources(  # noqa: SLF001
            "patient-fhir-id", payload, _operator(), "Approved reconstruction"
        )
    ]
    encounter = next(item for item in resources if item["resourceType"] == "Encounter")
    medication = next(item for item in resources if item["resourceType"] == "MedicationStatement")
    allergy = next(item for item in resources if item["resourceType"] == "AllergyIntolerance")
    follow_up = next(item for item in resources if item["resourceType"] == "ServiceRequest")
    questionnaire = next(item for item in resources if item["resourceType"] == "QuestionnaireResponse")
    projection = next(item for item in resources if item["resourceType"] == "Task")
    assert encounter["serviceType"]["text"] == "AI-assisted pre-visit check-in"
    assert medication["medicationCodeableConcept"]["text"] == "metformin"
    assert allergy["code"]["text"] == "penicillin"
    assert allergy["patient"] == {"reference": "Patient/patient-fhir-id"}
    assert follow_up["intent"] == "proposal"
    assert projection["code"]["text"] == "zep-demo-projection"
    assert demo._conditional_identifier(questionnaire).endswith(  # noqa: SLF001
        "|checkin-1:questionnaire-response"
    )


def test_operator_token_and_signing_key_must_be_separate(monkeypatch: pytest.MonkeyPatch) -> None:
    shared = "one-shared-secret-that-is-at-least-32-characters"
    monkeypatch.setenv("YC_DEMO_PATIENT_ID", "chart-1")
    monkeypatch.setenv("YC_DEMO_CHECKIN_SIGNING_KEY", shared)
    monkeypatch.setenv("YC_DEMO_ACCESS_TOKEN", shared)
    monkeypatch.setenv("YC_DEMO_OPERATOR_ID", "operator-1")
    monkeypatch.setenv("YC_DEMO_OPERATOR_NAME", "Dr. Reviewer")
    monkeypatch.setenv("ZEP_API_KEY", "zep-test")
    assert demo._workflow_status()["configured"] is False  # noqa: SLF001
    with pytest.raises(SponsorIntegrationError, match="must differ"):
        demo._signing_key()  # noqa: SLF001


def test_operator_token_is_compared_server_side(monkeypatch: pytest.MonkeyPatch) -> None:
    token = "runtime-only-demo-token-with-at-least-32-characters"
    monkeypatch.setenv("YC_DEMO_ACCESS_TOKEN", token)
    monkeypatch.setenv("YC_DEMO_OPERATOR_ID", "operator-1")
    monkeypatch.setenv("YC_DEMO_OPERATOR_NAME", "Dr. Reviewer")
    with pytest.raises(HTTPException) as wrong:
        demo._require_demo_operator("wrong-token")  # noqa: SLF001
    assert wrong.value.status_code == 403
    assert demo._require_demo_operator(token) == _operator()  # noqa: SLF001


def test_real_data_guard_requires_canonical_synthetic_patient(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signing = "signing-key-that-is-separate-and-at-least-32-chars"
    access = "access-key-that-is-separate-and-at-least-32-chars"
    env = {
        "YC_DEMO_PATIENT_ID": "chart-1",
        "YC_DEMO_CHECKIN_SIGNING_KEY": signing,
        "YC_DEMO_ACCESS_TOKEN": access,
        "YC_DEMO_OPERATOR_ID": "operator-1",
        "YC_DEMO_OPERATOR_NAME": "Dr. Reviewer",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    patient = {
        "resourceType": "Patient",
        "id": "chart-1",
        "name": [{"text": "Jane Doe"}],
        "birthDate": "2004-04-04",
        "identifier": [
            {"system": medplum.ZEP_USER_SYSTEM, "value": medplum.DEMO_ZEP_USER_ID}
        ],
        "meta": {
            "tag": [
                {"system": medplum.SYNTHETIC_TAG_SYSTEM, "code": code}
                for code in (
                    medplum.SYNTHETIC_TAG_CODE,
                    medplum.DEMO_TAG_CODE,
                    medplum.DEMO_STEDI_TAG_CODE,
                )
            ]
        },
    }

    class FakeCore:
        def read(self, resource_type: str, resource_id: str) -> dict[str, Any]:
            assert (resource_type, resource_id) == ("Patient", "chart-1")
            return patient

    class FakeRepo:
        @staticmethod
        def patient_view(resource: dict[str, Any]) -> dict[str, Any]:
            return {"id": resource["id"], "zep_user_id": medplum.DEMO_ZEP_USER_ID}

    monkeypatch.setattr(demo, "MedplumClient", lambda: medplum.MedplumClient(FakeCore()))
    monkeypatch.setattr(demo, "repository", lambda: FakeRepo())
    chart = asyncio.run(demo._require_real_data_layer("chart-1", _operator()))  # noqa: SLF001
    assert chart["id"] == "chart-1"
    patient["meta"]["tag"] = []
    with pytest.raises(HTTPException) as rejected:
        asyncio.run(demo._require_real_data_layer("chart-1", _operator()))  # noqa: SLF001
    assert rejected.value.status_code == 409


def test_public_demo_status_hides_an_unverified_patient_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("YC_DEMO_PATIENT_ID", "patient-1")
    monkeypatch.setattr(
        demo, "medplum_status", lambda: {"configured": True, "missing": []}
    )

    class FakeRepo:
        @staticmethod
        def get_patient(_patient_id: str) -> dict[str, Any]:
            return {
                "resourceType": "Patient",
                "id": "patient-1",
                "name": [{"text": "Real Person"}],
                "birthDate": "1980-01-01",
            }

    monkeypatch.setattr(demo, "repository", lambda: FakeRepo())
    assert demo.demo_status().demo_patient_id is None


def test_moss_session_name_keeps_nonce_and_rejects_endpoint_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patient_id = "chart-1"
    checkin_id = "checkin-1"
    name = moss_retrieval._index_name("x" * 100, patient_id, checkin_id)  # noqa: SLF001
    expected_suffix = hashlib.sha256(
        f"{patient_id}:{checkin_id}".encode()
    ).hexdigest()[:20]
    assert len(name) == 64
    assert name.endswith(expected_suffix)
    for key in ("MOSS_PROJECT_ID", "MOSS_PROJECT_KEY", "MOSS_INDEX_NAME", "MOSS_MODEL_ID"):
        monkeypatch.setenv(key, "configured")
    monkeypatch.setenv("MOSS_DISABLE_TELEMETRY", "true")
    assert moss_retrieval.configuration_status()["configured"] is False
    monkeypatch.setenv("MOSS_DISABLE_TELEMETRY", "1")
    monkeypatch.setenv("MOSS_QUERY_URL", "https://attacker.invalid")
    assert moss_retrieval.configuration_status()["configured"] is False


def test_deepgram_uses_current_diarize_model_parameter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPGRAM_API_KEY", "test-key")
    monkeypatch.setenv("DEEPGRAM_MODEL", "configured-model")
    monkeypatch.setenv("DEEPGRAM_DIARIZE_MODEL", "latest")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["diarize_model"] == "latest"
        assert "diarize" not in request.url.params
        return httpx.Response(
            200,
            json={
                "metadata": {"request_id": "dg-request", "duration": 4.0},
                "results": {
                    "utterances": [
                        {
                            "speaker": 0,
                            "start": 0,
                            "end": 4,
                            "transcript": "Patient evidence.",
                            "confidence": 0.99,
                        }
                    ]
                },
            },
        )

    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        deepgram.httpx,
        "AsyncClient",
        lambda *args, **kwargs: real_async_client(
            transport=httpx.MockTransport(handler), timeout=kwargs.get("timeout")
        ),
    )
    result = asyncio.run(deepgram.transcribe_audio(b"audio", content_type="audio/webm"))
    assert result["request_id"] == "dg-request"


def test_reviewed_evidence_must_come_from_selected_patient_speaker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _confirm_payload(monkeypatch)
    payload.utterances.append(
        type(payload.utterances[0]).model_validate(
            {
            "id": "dg-clinician",
            "speaker": 0,
            "start": 5,
            "end": 7,
            "text": "You missed metformin, correct?",
            "confidence": 0.99,
            }
        )
    )
    payload.checkin_token = demo._issue_checkin_token(  # noqa: SLF001
        checkin_id=payload.checkin_id,
        patient_id="chart-1",
        deepgram_request_id=payload.deepgram_request_id,
        openai_response_id=payload.openai_response_id,
        patient_speaker=payload.patient_speaker,
        utterances=[item.model_dump(mode="json") for item in payload.utterances],
        draft=payload.source_draft.model_dump(mode="json"),
    )
    payload.draft.proposed_changes[0].evidence_utterance_id = "dg-clinician"
    payload.draft.proposed_changes[0].evidence_quote = "missed metformin"
    with pytest.raises(SponsorIntegrationError, match="unknown utterance"):
        demo._validate_reviewed_draft(payload)  # noqa: SLF001


def test_medplum_rejects_short_transaction_response() -> None:
    class FakeCore:
        @staticmethod
        def transaction(_entries: list[dict[str, Any]]) -> dict[str, Any]:
            return {"resourceType": "Bundle", "type": "transaction-response", "entry": []}

    client = medplum.MedplumClient(FakeCore())
    with pytest.raises(SponsorIntegrationError, match="cardinality"):
        asyncio.run(
            client.transact(
                [{"resource": {"resourceType": "Encounter"}, "request": {"method": "POST"}}],
                checkin_id="checkin-1",
            )
        )


def test_incomplete_workflow_cannot_report_readiness(monkeypatch: pytest.MonkeyPatch) -> None:
    row = {
        "doc_id": "checkin-1",
        "metadata": {
            "yc_demo_checkin": True,
            "workflow_state": "approval_recorded",
            "validation_status": "pending",
        },
    }
    monkeypatch.setattr(demo, "_find_checkin_document", lambda *_, **__: row)
    with pytest.raises(HTTPException) as incomplete:
        asyncio.run(
            demo.readiness(
                "chart-1", _operator(), {"zep_user_id": "synthetic-user"}
            )
        )
    assert incomplete.value.status_code == 409


def test_medplum_validation_rejects_error_operation_outcome() -> None:
    class FakeCore:
        @staticmethod
        def validate(_resource: dict[str, Any]) -> dict[str, Any]:
            return {
                "resourceType": "OperationOutcome",
                "issue": [{"severity": "error", "diagnostics": "Invalid synthetic resource"}],
            }

    client = medplum.MedplumClient(FakeCore())
    with pytest.raises(SponsorIntegrationError, match="did not validate"):
        asyncio.run(client.validate_resources([{"resourceType": "Encounter"}]))


def test_journal_lookup_is_scoped_to_patient_and_identifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    metadata = {
        "yc_demo_checkin": True,
        "checkin_id": "checkin-1",
        "workflow_state": "complete",
        "validation_status": "passed",
        "medplum_resources": [{"resource_type": "Encounter", "resource_id": "e1", "status": "200"}],
    }
    resource = {
        "resourceType": "DocumentReference",
        "id": "journal-1",
        "identifier": [
            {"system": demo._CHECKIN_IDENTIFIER_SYSTEM, "value": "checkin-1:reconstruction"}  # noqa: SLF001
        ],
        "meta": {
            "tag": [{"system": demo.TAG_SYSTEM, "code": demo._CHECKIN_JOURNAL_TAG}]  # noqa: SLF001
        },
        "subject": {"reference": "Patient/patient-1"},
        "content": [
            {
                "attachment": {
                    "contentType": "application/json",
                    "data": base64.b64encode(json.dumps(metadata).encode()).decode(),
                }
            }
        ],
    }

    class FakeClient:
        @staticmethod
        def search(resource_type: str, params: dict[str, Any]) -> list[dict[str, Any]]:
            captured.update({"resource_type": resource_type, "params": params})
            return [resource]

    class FakeRepo:
        client = FakeClient()

    monkeypatch.setattr(demo, "repository", lambda: FakeRepo())
    row = demo._find_checkin_document("patient-1", "checkin-1")  # noqa: SLF001
    assert row and row["doc_id"] == "journal-1"
    assert captured["params"]["subject"] == "Patient/patient-1"
    assert captured["params"]["identifier"].endswith("|checkin-1:reconstruction")


def test_eligibility_retry_uses_existing_medplum_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = {
        "transaction_id": "stedi-1",
        "trace_id": "trace-1",
        "application_mode": "test",
        "coverage_active": True,
        "plan_status": [],
        "benefits": [],
        "patient_responsibility_summary": "Not determinable from test-mode eligibility alone.",
        "disclaimer": "Synthetic test data.",
    }
    eligibility_doc = {
        "resourceType": "DocumentReference",
        "id": "eligibility-1",
        "meta": {"versionId": "2"},
        "content": [
            {
                "attachment": {
                    "contentType": "application/json",
                    "data": base64.b64encode(json.dumps(result).encode()).decode(),
                }
            }
        ],
    }
    journal = {
        "doc_id": "journal-1",
        "metadata": {
            "yc_demo_checkin": True,
            "workflow_state": "complete",
            "validation_status": "passed",
            "medplum_resources": [{"resource_type": "Encounter", "resource_id": "e1", "status": "200"}],
        },
    }
    monkeypatch.setattr(demo, "_enforce_sponsor_rate_limit", lambda *_: None)
    monkeypatch.setattr(demo, "_find_checkin_document", lambda *_, **__: journal)
    monkeypatch.setattr(demo, "_find_eligibility_document", lambda *_: eligibility_doc)

    async def fail_if_called() -> dict[str, Any]:
        raise AssertionError("Stedi must not be called after its FHIR document exists")

    monkeypatch.setattr(demo, "check_eligibility", fail_if_called)
    monkeypatch.setattr(
        demo,
        "_update_document_metadata",
        lambda *_: {"doc_id": "journal-1", "metadata": journal["metadata"]},
    )
    response = asyncio.run(
        demo.eligibility(
            "patient-1",
            type("Payload", (), {"checkin_id": "checkin-1"})(),
            _operator(),
            {"zep_user_id": medplum.DEMO_ZEP_USER_ID},
        )
    )
    assert response.transaction_id == "stedi-1"
    assert response.medplum_resource.resource_id == "eligibility-1"


def test_zep_worker_marks_provider_failure_for_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completed: list[str | None] = []

    class FakeRepo:
        @staticmethod
        def get_patient(_patient_id: str) -> dict[str, Any]:
            return {
                "id": "patient-1",
                "identifier": [
                    {"system": medplum.ZEP_USER_SYSTEM, "value": medplum.DEMO_ZEP_USER_ID}
                ],
                "name": [{"text": "Jane Doe"}],
            }

        @staticmethod
        def complete_projection_task(_task: dict[str, Any], *, error: str | None = None, episode_count: int = 0) -> None:
            del episode_count
            completed.append(error)

        @staticmethod
        def abandon_projection_task(_task: dict[str, Any], *, reason: str) -> None:
            raise AssertionError(reason)

    monkeypatch.setattr(medplum_zep_worker, "repository", lambda: FakeRepo())
    monkeypatch.setattr(
        medplum_zep_worker,
        "ensure_user",
        lambda *_: (_ for _ in ()).throw(RuntimeError("Zep unavailable")),
    )
    task = {
        "code": {"text": "zep-demo-projection"},
        "for": {"reference": "Patient/patient-1"},
        "focus": {"reference": "DocumentReference/journal-1"},
        "input": [{"type": {"text": "note-text"}, "valueString": "Approved note"}],
    }
    assert medplum_zep_worker.process_task(task) is False
    assert completed == ["Zep projection failed; the worker will retry."]


def test_zep_worker_abandons_patient_without_projection_identifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    abandoned: list[str] = []

    class FakeRepo:
        @staticmethod
        def get_patient(_patient_id: str) -> dict[str, Any]:
            return {"id": "patient-1", "name": [{"text": "Jane Doe"}]}

        @staticmethod
        def abandon_projection_task(_task: dict[str, Any], *, reason: str) -> None:
            abandoned.append(reason)

    monkeypatch.setattr(medplum_zep_worker, "repository", lambda: FakeRepo())
    assert medplum_zep_worker.process_task(
        {"for": {"reference": "Patient/patient-1"}, "code": {"text": "zep-demo-projection"}}
    ) is False
    assert abandoned == ["Patient is missing its Zep projection identifier."]


def test_demo_provisioner_is_idempotent_and_uses_canonical_patient_id(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    identifiers: list[str] = []
    patient_calls: list[dict[str, Any]] = []

    class FakeClient:
        @staticmethod
        def conditional_upsert(
            resource: dict[str, Any], *, identifier: str
        ) -> dict[str, Any]:
            identifiers.append(identifier)
            return {**resource, "id": identifier.rsplit("|", 1)[-1].replace(":", "-")}

    class FakeRepo:
        client = FakeClient()

        @staticmethod
        def upsert_patient(**kwargs: Any) -> dict[str, Any]:
            patient_calls.append(kwargs)
            return {"resourceType": "Patient", "id": "patient-1"}

    monkeypatch.delenv("YC_DEMO_PATIENT_ID", raising=False)
    monkeypatch.setattr(provision_yc_demo_patient, "load_repo_env", lambda: None)
    monkeypatch.setattr(provision_yc_demo_patient, "medplum_configured", lambda: True)
    monkeypatch.setattr(provision_yc_demo_patient, "repository", lambda: FakeRepo())
    monkeypatch.setattr(
        provision_yc_demo_patient,
        "validate_synthetic_patient",
        lambda patient, patient_id: patient,
    )
    assert provision_yc_demo_patient.main() == 0
    assert provision_yc_demo_patient.main() == 0
    assert patient_calls[0]["tags"] == [
        medplum.SYNTHETIC_TAG_CODE,
        medplum.DEMO_TAG_CODE,
        medplum.DEMO_STEDI_TAG_CODE,
    ]
    assert len(set(identifiers)) == 6
    assert "YC_DEMO_PATIENT_ID=patient-1" in capsys.readouterr().out
