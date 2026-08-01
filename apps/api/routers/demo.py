"""Real-provider orchestration for the YC Medplum hackathon demo route."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import threading
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Security, UploadFile, status
from fastapi.security import APIKeyHeader

from apps.api.routers.patients import get_snapshot
from apps.api.schemas import (
    DemoCheckinOut,
    DemoConfirmIn,
    DemoConfirmOut,
    DemoEligibilityIn,
    DemoEligibilityOut,
    DemoProviderStatus,
    DemoReadinessOut,
    DemoStatusOut,
)
from medtrace_agent.agents.previsit import (
    PrevisitDraft,
    configuration_status as openai_status,
    create_previsit_draft,
    validate_evidence,
)
from medtrace_agent.ingest.documents import ingest_plain_text_note_to_patient_graph
from medtrace_agent.insforge_api import (
    documents_bucket,
    fetch_documents_registry,
    get_chart_subject,
    insert_document_record,
    remote_insforge_configured,
    update_document_metadata,
    upload_bytes_to_bucket,
)
from medtrace_agent.integrations.deepgram import (
    configuration_status as deepgram_status,
    transcribe_audio,
)
from medtrace_agent.integrations.medplum import (
    MedplumClient,
    configuration_status as medplum_status,
    patient_reference,
)
from medtrace_agent.integrations.moss_retrieval import (
    configuration_status as moss_status,
    retrieve_context,
)
from medtrace_agent.integrations.sponsor_error import SponsorIntegrationError
from medtrace_agent.integrations.stedi import (
    TEST_CASE_ID as STEDI_TEST_CASE_ID,
    check_eligibility,
    configuration_status as stedi_status,
)
from medtrace_agent.local_store import local_mock_enabled
from medtrace_agent.zep.graph import list_recent_episodes

router = APIRouter(prefix="/api/demo", tags=["yc-medplum-demo"])

_MAX_AUDIO_BYTES = 25 * 1024 * 1024
_CHECKIN_IDENTIFIER_SYSTEM = "https://github.com/ayushozha/medtrace-previsit/checkins"
_ELIGIBILITY_IDENTIFIER_SYSTEM = (
    "https://github.com/ayushozha/medtrace-previsit/stedi-eligibility"
)
_OPERATOR_IDENTIFIER_SYSTEM = "https://github.com/ayushozha/medtrace-previsit/operators"
_CHECKIN_TOKEN_TTL_SECONDS = 2 * 60 * 60
_DEMO_TOKEN_HEADER = "X-MedTrace-Demo-Token"
_SPONSOR_RATE_LIMIT = 8
_SPONSOR_RATE_WINDOW_SECONDS = 60.0
_demo_token_scheme = APIKeyHeader(
    name=_DEMO_TOKEN_HEADER,
    scheme_name="MedTraceDemoToken",
    description="Runtime-only operator token for the synthetic hackathon workflow.",
    auto_error=False,
)
_sponsor_calls: dict[str, deque[float]] = defaultdict(deque)
_sponsor_calls_lock = threading.Lock()

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DemoOperator:
    operator_id: str
    display_name: str


def _provider_status(raw: dict[str, object]) -> DemoProviderStatus:
    return DemoProviderStatus.model_validate(raw)


def _workflow_status() -> dict[str, object]:
    missing: list[str] = []
    patient_id = (os.environ.get("YC_DEMO_PATIENT_ID") or "").strip()
    signing_key = (os.environ.get("YC_DEMO_CHECKIN_SIGNING_KEY") or "").strip()
    access_token = (os.environ.get("YC_DEMO_ACCESS_TOKEN") or "").strip()
    operator_id = (os.environ.get("YC_DEMO_OPERATOR_ID") or "").strip()
    operator_name = (os.environ.get("YC_DEMO_OPERATOR_NAME") or "").strip()
    if not patient_id:
        missing.append("YC_DEMO_PATIENT_ID")
    if len(signing_key.encode("utf-8")) < 32:
        missing.append("YC_DEMO_CHECKIN_SIGNING_KEY (at least 32 characters)")
    if len(access_token.encode("utf-8")) < 32:
        missing.append("YC_DEMO_ACCESS_TOKEN (at least 32 characters)")
    if signing_key and access_token and hmac.compare_digest(signing_key, access_token):
        missing.append("YC_DEMO_ACCESS_TOKEN must differ from YC_DEMO_CHECKIN_SIGNING_KEY")
    if not operator_id:
        missing.append("YC_DEMO_OPERATOR_ID")
    if not operator_name:
        missing.append("YC_DEMO_OPERATOR_NAME")
    if not (os.environ.get("ZEP_API_KEY") or "").strip():
        missing.append("ZEP_API_KEY")
    return {"configured": not missing, "missing": missing}


def _require_demo_operator(
    supplied_token: Annotated[str | None, Security(_demo_token_scheme)],
) -> DemoOperator:
    configured_token = (os.environ.get("YC_DEMO_ACCESS_TOKEN") or "").strip()
    if len(configured_token.encode("utf-8")) < 32:
        raise HTTPException(status_code=503, detail="The demo operator gate is not configured.")
    if not supplied_token:
        raise HTTPException(
            status_code=401,
            detail=f"{_DEMO_TOKEN_HEADER} is required.",
            headers={"WWW-Authenticate": "MedTraceDemoToken"},
        )
    if not hmac.compare_digest(supplied_token, configured_token):
        raise HTTPException(status_code=403, detail="The demo operator token is invalid.")
    operator_id = (os.environ.get("YC_DEMO_OPERATOR_ID") or "").strip()
    display_name = (os.environ.get("YC_DEMO_OPERATOR_NAME") or "").strip()
    if not operator_id or not display_name:
        raise HTTPException(status_code=503, detail="The server-side demo operator identity is incomplete.")
    return DemoOperator(operator_id=operator_id, display_name=display_name)


def _enforce_sponsor_rate_limit(operator: DemoOperator) -> None:
    now = time.monotonic()
    with _sponsor_calls_lock:
        calls = _sponsor_calls[operator.operator_id]
        while calls and now - calls[0] >= _SPONSOR_RATE_WINDOW_SECONDS:
            calls.popleft()
        if len(calls) >= _SPONSOR_RATE_LIMIT:
            raise HTTPException(
                status_code=429,
                detail="The demo sponsor-call limit was reached; wait one minute and retry.",
            )
        calls.append(now)


def _require_real_data_layer(
    patient_id: str,
    _operator: Annotated[DemoOperator, Depends(_require_demo_operator)],
) -> dict[str, Any]:
    if local_mock_enabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "The hackathon route does not use MEDTRACE_LOCAL_MOCK. Configure the real "
                "InsForge database and select a synthetic demo patient."
            ),
        )
    if not remote_insforge_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Real InsForge persistence is required for the hackathon route.",
        )
    configured_patient_id = (os.environ.get("YC_DEMO_PATIENT_ID") or "").strip()
    if not configured_patient_id:
        raise HTTPException(status_code=503, detail="YC_DEMO_PATIENT_ID is required.")
    if not hmac.compare_digest(patient_id, configured_patient_id):
        raise HTTPException(status_code=404, detail="Synthetic demo patient not found.")
    workflow = _workflow_status()
    if not workflow["configured"]:
        raise HTTPException(
            status_code=503,
            detail=f"Demo workflow configuration is incomplete: {', '.join(workflow['missing'])}.",
        )
    chart = get_chart_subject(chart_subject_id=patient_id)
    if not chart:
        raise HTTPException(status_code=404, detail="Synthetic demo patient not found.")
    metadata = chart.get("metadata") if isinstance(chart.get("metadata"), dict) else {}
    fields = metadata.get("fields") if isinstance(metadata.get("fields"), dict) else {}
    tags = metadata.get("tags") or fields.get("tags") or []
    binding = metadata.get("yc_medplum_demo")
    if (
        "synthetic" not in {str(tag).strip().lower() for tag in tags if tag}
        or not isinstance(binding, dict)
        or binding.get("synthetic") is not True
    ):
        raise HTTPException(
            status_code=403,
            detail="The configured chart is not marked as an approved synthetic demo patient.",
        )
    medplum_patient_id = (os.environ.get("MEDPLUM_PATIENT_ID") or "").strip()
    if not medplum_patient_id or not hmac.compare_digest(
        str(binding.get("medplum_patient_id") or ""), medplum_patient_id
    ):
        raise HTTPException(
            status_code=409,
            detail="The InsForge chart is not bound to the configured Medplum synthetic patient.",
        )
    if (
        binding.get("stedi_test_case") != STEDI_TEST_CASE_ID
        or str(chart.get("display_name") or "").strip() != "Jane Doe"
        or str(fields.get("dob") or "").strip() != "2004-04-04"
    ):
        raise HTTPException(
            status_code=409,
            detail="The chart is not bound to the documented Stedi Aetna Jane Doe test persona.",
        )
    return chart


DemoOperatorDep = Annotated[DemoOperator, Depends(_require_demo_operator)]
DemoChartDep = Annotated[dict[str, Any], Depends(_require_real_data_layer)]


def _raise_sponsor(exc: SponsorIntegrationError) -> None:
    logger.warning(
        "Sponsor integration failed provider=%s status=%s detail=%s",
        exc.provider,
        exc.status_code,
        exc.detail,
        exc_info=exc,
    )
    detail = (
        f"{exc.provider.title()}: {exc.detail}"
        if exc.status_code < 500 or exc.status_code == 503
        else f"{exc.provider.title()} request failed; inspect the API logs for details."
    )
    raise HTTPException(
        status_code=exc.status_code,
        detail=detail,
    ) from exc


@router.get("/status", response_model=DemoStatusOut)
def demo_status() -> DemoStatusOut:
    if local_mock_enabled():
        data_mode = "local-mock"
    elif remote_insforge_configured():
        data_mode = "remote"
    else:
        data_mode = "unconfigured"
    return DemoStatusOut(
        demo_patient_id=(os.environ.get("YC_DEMO_PATIENT_ID") or "").strip() or None,
        data_mode=data_mode,  # type: ignore[arg-type]
        deepgram=_provider_status(deepgram_status()),
        moss=_provider_status(moss_status()),
        openai=_provider_status(openai_status()),
        medplum=_provider_status(medplum_status()),
        stedi=_provider_status(stedi_status()),
        workflow=_provider_status(_workflow_status()),
    )


def _signing_key() -> bytes:
    key = (os.environ.get("YC_DEMO_CHECKIN_SIGNING_KEY") or "").encode("utf-8")
    if len(key) < 32:
        raise SponsorIntegrationError(
            "workflow",
            "YC_DEMO_CHECKIN_SIGNING_KEY must contain at least 32 characters.",
            status_code=503,
        )
    access_token = (os.environ.get("YC_DEMO_ACCESS_TOKEN") or "").encode("utf-8")
    if access_token and hmac.compare_digest(key, access_token):
        raise SponsorIntegrationError(
            "workflow",
            "YC_DEMO_CHECKIN_SIGNING_KEY must differ from YC_DEMO_ACCESS_TOKEN.",
            status_code=503,
        )
    return key


def _issue_checkin_token(
    *,
    checkin_id: str,
    patient_id: str,
    deepgram_request_id: str,
    openai_response_id: str,
    patient_speaker: int,
    utterances: list[dict[str, Any]],
    draft: dict[str, Any],
) -> str:
    evidence_digest = _evidence_digest(
        checkin_id=checkin_id,
        patient_id=patient_id,
        deepgram_request_id=deepgram_request_id,
        openai_response_id=openai_response_id,
        patient_speaker=patient_speaker,
        utterances=utterances,
        draft=draft,
    )
    body = json.dumps(
        {
            "checkin_id": checkin_id,
            "patient_id": patient_id,
            "deepgram_request_id": deepgram_request_id,
            "openai_response_id": openai_response_id,
            "patient_speaker": patient_speaker,
            "evidence_digest": evidence_digest,
            "expires_at": int(time.time()) + _CHECKIN_TOKEN_TTL_SECONDS,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    encoded = base64.urlsafe_b64encode(body).rstrip(b"=")
    signature = hmac.new(_signing_key(), encoded, hashlib.sha256).digest()
    encoded_signature = base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
    return f"{encoded.decode('ascii')}.{encoded_signature}"


def _decode_checkin_token(token: str) -> dict[str, Any]:
    try:
        encoded, supplied_signature = token.split(".", 1)
        encoded_bytes = encoded.encode("ascii")
        expected_signature = hmac.new(_signing_key(), encoded_bytes, hashlib.sha256).digest()
        supplied = base64.urlsafe_b64decode(supplied_signature + "=" * (-len(supplied_signature) % 4))
        if not hmac.compare_digest(supplied, expected_signature):
            raise ValueError("signature mismatch")
        raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        payload = json.loads(raw)
    except (UnicodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise SponsorIntegrationError(
            "workflow", "The check-in evidence token is invalid.", status_code=409
        ) from exc
    if not isinstance(payload, dict) or int(payload.get("expires_at") or 0) < int(time.time()):
        raise SponsorIntegrationError(
            "workflow", "The check-in evidence token has expired.", status_code=409
        )
    return payload


def _evidence_digest(
    *,
    checkin_id: str,
    patient_id: str,
    deepgram_request_id: str,
    openai_response_id: str,
    patient_speaker: int,
    utterances: list[dict[str, Any]],
    draft: dict[str, Any],
) -> str:
    canonical = json.dumps(
        {
            "checkin_id": checkin_id,
            "patient_id": patient_id,
            "deepgram_request_id": deepgram_request_id,
            "openai_response_id": openai_response_id,
            "patient_speaker": patient_speaker,
            "utterances": utterances,
            "draft": draft,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _verify_checkin_token(patient_id: str, payload: DemoConfirmIn) -> None:
    source = _decode_checkin_token(payload.checkin_token)
    scalar_expected = {
        "checkin_id": payload.checkin_id,
        "patient_id": patient_id,
        "deepgram_request_id": payload.deepgram_request_id,
        "openai_response_id": payload.openai_response_id,
        "patient_speaker": payload.patient_speaker,
    }
    supplied_digest = _evidence_digest(
        **scalar_expected,
        utterances=[item.model_dump(mode="json") for item in payload.utterances],
        draft=payload.source_draft.model_dump(mode="json"),
    )
    if any(source.get(key) != value for key, value in scalar_expected.items()) or not hmac.compare_digest(
        str(source.get("evidence_digest") or ""), supplied_digest
    ):
        raise SponsorIntegrationError(
            "workflow",
            "The confirmation no longer matches the provider-produced check-in evidence.",
            status_code=409,
        )


def _validate_reviewed_draft(payload: DemoConfirmIn) -> PrevisitDraft:
    return validate_evidence(
        PrevisitDraft.model_validate(payload.draft.model_dump(mode="json")),
        [
            item.model_dump(mode="json")
            for item in payload.utterances
            if item.speaker == payload.patient_speaker
        ],
    )


def _snapshot_documents(snapshot: dict[str, Any], utterances: list[dict[str, Any]]) -> list[dict[str, Any]]:
    patient = snapshot.get("patient") or {}
    patient_id = str(patient.get("id") or "")
    docs: list[dict[str, Any]] = [
        {
            "id": "chart-summary",
            "text": str(patient.get("summary") or "No chart summary available."),
            "metadata": {"patient_id": patient_id, "source": "chart_summary"},
        }
    ]
    sections = (
        ("conditions", snapshot.get("active_conditions") or []),
        ("medications", snapshot.get("current_medications") or []),
        ("allergies", snapshot.get("allergies") or []),
        ("labs", snapshot.get("lab_trends") or []),
        ("timeline", snapshot.get("timeline") or []),
    )
    for name, values in sections:
        docs.append(
            {
                "id": f"chart-{name}",
                "text": json.dumps(values, ensure_ascii=False, default=str),
                "metadata": {"patient_id": patient_id, "source": name},
            }
        )
    docs.extend(
        {
            "id": str(item["id"]),
            "text": str(item["text"]),
            "metadata": {
                "patient_id": patient_id,
                "source": "deepgram_utterance",
                "speaker": str(item["speaker"]),
                "start": str(item["start"]),
            },
        }
        for item in utterances
    )
    return docs


@router.post(
    "/patients/{patient_id}/checkins",
    response_model=DemoCheckinOut,
)
async def create_checkin(
    patient_id: str,
    operator: DemoOperatorDep,
    chart: DemoChartDep,
    audio: UploadFile = File(..., alias="file"),
    patient_speaker: int = Form(default=0),
    duration_seconds: float | None = Form(default=None),
) -> DemoCheckinOut:
    del chart
    _enforce_sponsor_rate_limit(operator)
    if duration_seconds is not None and duration_seconds > 60:
        raise HTTPException(status_code=413, detail="The synthetic demo recording must be 60 seconds or shorter.")
    if not 0 <= patient_speaker <= 20:
        raise HTTPException(status_code=422, detail="patient_speaker must be between 0 and 20.")
    content_type = (audio.content_type or "application/octet-stream").lower()
    if not (content_type.startswith("audio/") or content_type in {"video/webm", "application/octet-stream"}):
        raise HTTPException(status_code=415, detail="Upload an audio recording supported by Deepgram.")
    raw = await audio.read(_MAX_AUDIO_BYTES + 1)
    if not raw:
        raise HTTPException(status_code=400, detail="The audio recording is empty.")
    if len(raw) > _MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="Audio exceeds the 25 MB demo limit.")

    snapshot = await asyncio.to_thread(get_snapshot, patient_id)
    try:
        deepgram = await transcribe_audio(raw, content_type=content_type)
        if not deepgram.get("request_id"):
            raise SponsorIntegrationError("deepgram", "Deepgram returned no request ID.")
        patient_utterances = [
            item for item in deepgram["utterances"] if item.get("speaker") == patient_speaker
        ]
        if not patient_utterances:
            raise SponsorIntegrationError(
                "deepgram",
                "The selected patient speaker has no diarized utterances.",
                status_code=422,
            )
        checkin_id = str(uuid.uuid4())
        moss = await retrieve_context(
            patient_id=patient_id,
            checkin_id=checkin_id,
            documents=_snapshot_documents(snapshot.model_dump(mode="json"), deepgram["utterances"]),
            query=(
                "What changed in medication adherence, allergies, worsening biometrics, and unresolved "
                "follow-up questions during today's pre-visit check-in?"
            ),
        )
        draft, openai_response_id = await create_previsit_draft(
            utterances=patient_utterances, retrieval=moss["evidence"]
        )
    except SponsorIntegrationError as exc:
        _raise_sponsor(exc)
    return DemoCheckinOut(
        checkin_id=checkin_id,
        patient_id=patient_id,
        deepgram_request_id=deepgram["request_id"],
        deepgram_model=deepgram["model"],
        openai_response_id=openai_response_id,
        patient_speaker=patient_speaker,
        checkin_token=_issue_checkin_token(
            checkin_id=checkin_id,
            patient_id=patient_id,
            deepgram_request_id=deepgram["request_id"],
            openai_response_id=openai_response_id,
            patient_speaker=patient_speaker,
            utterances=deepgram["utterances"],
            draft=draft.model_dump(mode="json"),
        ),
        utterances=deepgram["utterances"],
        moss=moss,
        draft=draft.model_dump(mode="json"),
    )


def _checkin_identifier(checkin_id: str, suffix: str) -> dict[str, str]:
    return {"system": _CHECKIN_IDENTIFIER_SYSTEM, "value": f"{checkin_id}:{suffix}"}


def _conditional_identifier(resource: dict[str, Any]) -> str:
    raw = resource.get("identifier")
    identifier = raw[0] if isinstance(raw, list) and raw else raw
    if not isinstance(identifier, dict) or not identifier.get("system") or not identifier.get("value"):
        raise SponsorIntegrationError(
            "medplum",
            f"{resource.get('resourceType') or 'FHIR resource'} has no conditional-create identifier.",
            status_code=500,
        )
    return f"identifier={identifier['system']}|{identifier['value']}"


def _approved_utterances(payload: DemoConfirmIn) -> list[dict[str, Any]]:
    cited_ids = {change.evidence_utterance_id for change in payload.draft.proposed_changes}
    return [
        item.model_dump(mode="json")
        for item in payload.utterances
        if item.id in cited_ids
    ]


def _json_digest(value: Any) -> str:
    canonical = json.dumps(
        value, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _review_audit(payload: DemoConfirmIn) -> dict[str, Any]:
    source = payload.source_draft.model_dump(mode="json")
    reviewed = payload.draft.model_dump(mode="json")
    differences: list[dict[str, Any]] = []

    def walk(before: Any, after: Any, path: str) -> None:
        if isinstance(before, dict) and isinstance(after, dict):
            for key in sorted(set(before) | set(after)):
                walk(before.get(key), after.get(key), f"{path}.{key}" if path else key)
        elif isinstance(before, list) and isinstance(after, list):
            for index in range(max(len(before), len(after))):
                walk(
                    before[index] if index < len(before) else None,
                    after[index] if index < len(after) else None,
                    f"{path}[{index}]",
                )
        elif before != after:
            differences.append({"path": path, "before": before, "after": after})

    walk(source, reviewed, "")
    return {
        "source_draft_sha256": _json_digest(source),
        "reviewed_draft_sha256": _json_digest(reviewed),
        "differences": differences,
    }


def _operator_reference(operator: DemoOperator) -> dict[str, Any]:
    return {
        "identifier": {"system": _OPERATOR_IDENTIFIER_SYSTEM, "value": operator.operator_id},
        "display": operator.display_name,
    }


def _fhir_resources(
    payload: DemoConfirmIn, operator: DemoOperator
) -> list[tuple[str, dict[str, Any]]]:
    now = datetime.now(timezone.utc).isoformat()
    patient = patient_reference()
    encounter_urn = f"urn:uuid:{uuid.uuid4()}"
    reconstruction = {
        "checkin_id": payload.checkin_id,
        "deepgram_request_id": payload.deepgram_request_id,
        "openai_response_id": payload.openai_response_id,
        "patient_speaker": payload.patient_speaker,
        "operator_id": operator.operator_id,
        "clinician_name": operator.display_name,
        "evidence_utterances": _approved_utterances(payload),
        "draft": payload.draft.model_dump(mode="json"),
        "review_audit": _review_audit(payload),
        "approved_at": now,
    }
    resources: list[tuple[str, dict[str, Any]]] = [
        (
            encounter_urn,
            {
                "resourceType": "Encounter",
                "identifier": [_checkin_identifier(payload.checkin_id, "encounter")],
                "status": "finished",
                "class": {
                    "system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
                    "code": "AMB",
                    "display": "ambulatory",
                },
                "serviceType": {"text": "AI-assisted pre-visit check-in"},
                "subject": {"reference": patient},
                "period": {"start": now, "end": now},
                "reasonCode": [{"text": "Clinician-reviewed pre-visit check-in"}],
            },
        ),
        (
            f"urn:uuid:{uuid.uuid4()}",
            {
                "resourceType": "QuestionnaireResponse",
                "identifier": _checkin_identifier(payload.checkin_id, "questionnaire-response"),
                "status": "completed",
                "subject": {"reference": patient},
                "encounter": {"reference": encounter_urn},
                "authored": now,
                "author": _operator_reference(operator),
                "item": [
                    {
                        "linkId": f"change-{index + 1}",
                        "text": change.title,
                        "answer": [
                            {
                                "valueString": (
                                    f"{change.proposed_value} Evidence {change.evidence_utterance_id}: "
                                    f'"{change.evidence_quote}"'
                                )
                            }
                        ],
                    }
                    for index, change in enumerate(payload.draft.proposed_changes)
                ],
            },
        ),
        (
            f"urn:uuid:{uuid.uuid4()}",
            {
                "resourceType": "DocumentReference",
                "identifier": [_checkin_identifier(payload.checkin_id, "reconstruction")],
                "status": "current",
                "type": {"text": "Clinician-approved pre-visit reconstruction"},
                "subject": {"reference": patient},
                "date": now,
                "author": [_operator_reference(operator)],
                "context": {"encounter": [{"reference": encounter_urn}]},
                "content": [
                    {
                        "attachment": {
                            "contentType": "application/json",
                            "title": f"Pre-visit reconstruction {payload.checkin_id}",
                            "data": base64.b64encode(
                                json.dumps(reconstruction, ensure_ascii=False).encode("utf-8")
                            ).decode("ascii"),
                        }
                    }
                ],
            },
        ),
    ]
    for index, change in enumerate(payload.draft.proposed_changes):
        common = {
            "identifier": [_checkin_identifier(payload.checkin_id, f"change-{index + 1}")],
            "subject": {"reference": patient},
            "note": [
                {
                    "text": (
                        f"Clinician-approved: {change.proposed_value}. Deepgram evidence "
                        f'{change.evidence_utterance_id}: "{change.evidence_quote}"'
                    )
                }
            ],
        }
        if change.kind == "medication_adherence":
            resource = {
                "resourceType": "MedicationStatement",
                **common,
                "status": "active",
                "medicationCodeableConcept": {"text": change.clinical_subject},
                "context": {"reference": encounter_urn},
                "dateAsserted": now,
            }
        elif change.kind == "allergy_confirmation":
            resource = {
                "resourceType": "AllergyIntolerance",
                "identifier": common["identifier"],
                "patient": common["subject"],
                "note": common["note"],
                "verificationStatus": {
                    "coding": [
                        {
                            "system": "http://terminology.hl7.org/CodeSystem/allergyintolerance-verification",
                            "code": "confirmed",
                        }
                    ]
                },
                "code": {"text": change.clinical_subject},
                "encounter": {"reference": encounter_urn},
                "recordedDate": now,
                "reaction": [{"manifestation": [{"text": change.proposed_value}]}],
            }
        else:
            resource = {
                "resourceType": "ServiceRequest",
                "identifier": common["identifier"],
                "status": "active",
                "intent": "proposal",
                "code": {"text": change.clinical_subject},
                "subject": common["subject"],
                "encounter": {"reference": encounter_urn},
                "authoredOn": now,
                "reasonCode": [{"text": change.proposed_value}],
                "note": common["note"],
            }
        resources.append((f"urn:uuid:{uuid.uuid4()}", resource))
    return resources


def _note_text(payload: DemoConfirmIn, operator: DemoOperator) -> str:
    lines = [
        "# Clinician-approved pre-visit reconstruction",
        "",
        f"Check-in: {payload.checkin_id}",
        f"Deepgram request: {payload.deepgram_request_id}",
        f"Clinician: {operator.display_name}",
        f"Operator ID: {operator.operator_id}",
        "",
        "## What changed today",
    ]
    for change in payload.draft.proposed_changes:
        lines.extend(
            [
                f"- {change.title}: {change.proposed_value}",
                f'  Evidence {change.evidence_utterance_id}: "{change.evidence_quote}"',
            ]
        )
    lines.extend(["", "## What the clinician should verify"])
    lines.extend(f"- {item}" for item in payload.draft.clinician_verification)
    lines.extend(["", "## Unresolved questions"])
    lines.extend(f"- {item}" for item in payload.draft.unresolved_questions)
    lines.extend(["", "## Cited Deepgram evidence"])
    lines.extend(
        f"- [{item.start:.2f}-{item.end:.2f}] Speaker {item.speaker + 1}: {item.text}"
        for item in payload.utterances
        if item.id in {change.evidence_utterance_id for change in payload.draft.proposed_changes}
    )
    return "\n".join(lines).strip() + "\n"


def _find_checkin_document(
    patient_id: str,
    checkin_id: str | None = None,
    *,
    completed_only: bool = False,
) -> dict[str, Any] | None:
    matches: list[dict[str, Any]] = []
    for row in fetch_documents_registry(chart_subject_id=patient_id):
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        if not metadata.get("yc_demo_checkin"):
            continue
        if checkin_id and str(metadata.get("checkin_id")) != checkin_id:
            continue
        if completed_only and metadata.get("workflow_state") != "complete":
            continue
        matches.append(row)
    return max(
        matches,
        key=lambda row: str(
            ((row.get("metadata") or {}).get("approved_at") if isinstance(row.get("metadata"), dict) else "")
            or row.get("uploaded_at")
            or ""
        ),
        default=None,
    )


def _completed_metadata(row: dict[str, Any]) -> dict[str, Any]:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    resources = metadata.get("medplum_resources")
    if (
        metadata.get("workflow_state") != "complete"
        or metadata.get("validation_status") != "passed"
        or not isinstance(resources, list)
        or not resources
    ):
        raise HTTPException(
            status_code=409,
            detail="The referenced check-in has not completed validated FHIR persistence.",
        )
    return metadata


def _saved_confirmation(
    row: dict[str, Any], payload: DemoConfirmIn, operator: DemoOperator
) -> DemoConfirmOut:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    review_audit = metadata.get("review_audit") if isinstance(metadata.get("review_audit"), dict) else {}
    if (
        metadata.get("draft") != payload.draft.model_dump(mode="json")
        or str(metadata.get("operator_id") or "") != operator.operator_id
        or str(review_audit.get("source_draft_sha256") or "")
        != _json_digest(payload.source_draft.model_dump(mode="json"))
    ):
        raise HTTPException(
            status_code=409,
            detail="This check-in was already approved with different reviewed content.",
        )
    return DemoConfirmOut(
        checkin_id=payload.checkin_id,
        approved=True,
        validation_status="passed",
        validations=metadata.get("validations") or [],
        resources=metadata.get("medplum_resources") or [],
        document_id=str(row.get("doc_id") or payload.checkin_id),
        episode_ids=metadata.get("episode_ids") or [],
    )


def _find_zep_checkin_episodes(user_id: str, checkin_id: str) -> list[str]:
    marker = f"doc_id={checkin_id} "
    return [
        str(row.get("uuid"))
        for row in list_recent_episodes(user_id, lastn=100, truncate_chars=None)
        if row.get("uuid") and marker in str(row.get("content") or "")
    ]


@router.post(
    "/patients/{patient_id}/checkins/confirm",
    response_model=DemoConfirmOut,
)
async def confirm_checkin(
    patient_id: str,
    payload: DemoConfirmIn,
    operator: DemoOperatorDep,
    chart: DemoChartDep,
) -> DemoConfirmOut:
    _enforce_sponsor_rate_limit(operator)
    if not payload.approved:
        raise HTTPException(status_code=409, detail="No FHIR write occurs until the clinician approves.")
    try:
        _verify_checkin_token(patient_id, payload)
        draft = _validate_reviewed_draft(payload)
        payload.draft = payload.draft.model_validate(draft.model_dump(mode="json"))
    except SponsorIntegrationError as exc:
        _raise_sponsor(exc)

    note_text = _note_text(payload, operator)
    note_bytes = note_text.encode("utf-8")
    filename = f"previsit-{payload.checkin_id}.txt"
    existing = await asyncio.to_thread(_find_checkin_document, patient_id, payload.checkin_id)
    if existing:
        metadata = existing.get("metadata") if isinstance(existing.get("metadata"), dict) else {}
        if (
            metadata.get("draft") != payload.draft.model_dump(mode="json")
            or str(metadata.get("operator_id") or "") != operator.operator_id
        ):
            raise HTTPException(
                status_code=409,
                detail="This check-in was already approved with different reviewed content.",
            )
        if metadata.get("workflow_state") == "complete":
            return _saved_confirmation(existing, payload, operator)
    else:
        try:
            upload = await asyncio.to_thread(
                upload_bytes_to_bucket, note_bytes, filename, content_type="text/plain"
            )
            metadata = {
                "yc_demo_checkin": True,
                "checkin_id": payload.checkin_id,
                "approved_at": datetime.now(timezone.utc).isoformat(),
                "operator_id": operator.operator_id,
                "clinician_name": operator.display_name,
                "deepgram_request_id": payload.deepgram_request_id,
                "openai_response_id": payload.openai_response_id,
                "patient_speaker": payload.patient_speaker,
                "draft": payload.draft.model_dump(mode="json"),
                "review_audit": _review_audit(payload),
                "evidence_utterances": _approved_utterances(payload),
                "workflow_state": "approval_recorded",
            }
            existing = await asyncio.to_thread(
                insert_document_record,
                doc_id=payload.checkin_id,
                filename=filename,
                document_kind="conversation_note",
                storage_bucket=str(upload.get("bucket") or documents_bucket()),
                storage_key=str(upload.get("key") or ""),
                storage_url=str(upload.get("url") or "") or None,
                chart_subject_id=patient_id,
                extract_mode="deepgram_moss_openai_medplum",
                episode_count=0,
                metadata=metadata,
            )
            if not existing:
                raise RuntimeError("InsForge did not return the approval journal record.")
        except Exception as exc:
            logger.exception("Failed to persist approval journal checkin_id=%s", payload.checkin_id)
            raise HTTPException(
                status_code=502,
                detail="The approval journal could not be saved; no Medplum write was attempted.",
            ) from exc

    metadata = existing.get("metadata") if isinstance(existing.get("metadata"), dict) else metadata
    validations = metadata.get("validations") if isinstance(metadata.get("validations"), list) else []
    resources = (
        metadata.get("medplum_resources")
        if isinstance(metadata.get("medplum_resources"), list)
        else []
    )
    if not resources:
        try:
            fhir_pairs = _fhir_resources(payload, operator)
            fhir_resources = [resource for _, resource in fhir_pairs]
            medplum = MedplumClient()
            await medplum.assert_synthetic_patient(patient_id)
            validations = await medplum.validate_resources(fhir_resources)
            entries = [
                {
                    "fullUrl": full_url,
                    "resource": resource,
                    "request": {
                        "method": "POST",
                        "url": resource["resourceType"],
                        "ifNoneExist": _conditional_identifier(resource),
                    },
                }
                for full_url, resource in fhir_pairs
            ]
            resources = await medplum.transact(entries, checkin_id=payload.checkin_id)
        except SponsorIntegrationError as exc:
            _raise_sponsor(exc)
        saved = await asyncio.to_thread(
            update_document_metadata,
            doc_id=payload.checkin_id,
            metadata_patch={
                "validation_status": "passed",
                "validations": validations,
                "medplum_resources": resources,
                "workflow_state": "medplum_committed",
            },
        )
        if not saved:
            raise HTTPException(
                status_code=502,
                detail=(
                    "Medplum committed the conditional FHIR transaction, but the InsForge workflow "
                    "journal could not be updated. Retry with the same check-in ID."
                ),
            )
        existing = saved

    zep_user_id = str(chart.get("zep_user_id") or "")
    episode_ids = await asyncio.to_thread(
        _find_zep_checkin_episodes, zep_user_id, payload.checkin_id
    )
    if not episode_ids:
        try:
            episode_ids = await asyncio.to_thread(
                ingest_plain_text_note_to_patient_graph,
                zep_user_id,
                note_text,
                note_source="session_note",
                filename=filename,
                doc_id=payload.checkin_id,
                extra_metadata={"checkin_id": payload.checkin_id, "medplum_validated": True},
            )
        except Exception as exc:
            logger.exception("Zep reconstruction ingest failed checkin_id=%s", payload.checkin_id)
            raise HTTPException(
                status_code=502,
                detail=(
                    "Medplum is committed and the approval journal is durable, but Zep chart refresh "
                    "failed. Retry with the same check-in ID."
                ),
            ) from exc

    try:
        completed = await asyncio.to_thread(
            update_document_metadata,
            doc_id=payload.checkin_id,
            metadata_patch={"episode_ids": episode_ids, "workflow_state": "complete"},
        )
        if not completed:
            raise RuntimeError("InsForge did not persist the completed workflow state.")
    except Exception as exc:
        logger.exception("Failed to finalize workflow journal checkin_id=%s", payload.checkin_id)
        raise HTTPException(
            status_code=502,
            detail=(
                "Medplum and Zep succeeded, but the workflow journal could not be finalized. "
                "Retry with the same check-in ID."
            ),
        ) from exc
    return DemoConfirmOut(
        checkin_id=payload.checkin_id,
        approved=True,
        validation_status="passed",
        validations=validations,
        resources=resources,
        document_id=payload.checkin_id,
        episode_ids=episode_ids,
    )


def _eligibility_document(checkin_id: str, result: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    retained = {
        key: result.get(key)
        for key in (
            "transaction_id",
            "trace_id",
            "application_mode",
            "coverage_active",
            "plan_status",
            "benefits",
            "patient_responsibility_summary",
            "disclaimer",
        )
    }
    return {
        "resourceType": "DocumentReference",
        "identifier": [{"system": _ELIGIBILITY_IDENTIFIER_SYSTEM, "value": checkin_id}],
        "status": "current",
        "type": {"text": "Stedi test-mode eligibility response"},
        "subject": {"reference": patient_reference()},
        "date": now,
        "author": [{"display": "Stedi test mode"}],
        "content": [
            {
                "attachment": {
                    "contentType": "application/json",
                    "title": f"Normalized Stedi test eligibility {checkin_id}",
                    "data": base64.b64encode(
                        json.dumps(retained, ensure_ascii=False).encode("utf-8")
                    ).decode("ascii"),
                }
            }
        ],
    }


@router.post(
    "/patients/{patient_id}/eligibility",
    response_model=DemoEligibilityOut,
)
async def eligibility(
    patient_id: str,
    payload: DemoEligibilityIn,
    operator: DemoOperatorDep,
    chart: DemoChartDep,
) -> DemoEligibilityOut:
    del chart
    _enforce_sponsor_rate_limit(operator)
    document = await asyncio.to_thread(_find_checkin_document, patient_id, payload.checkin_id)
    if not document:
        raise HTTPException(status_code=409, detail="Approve and save the pre-visit check-in first.")
    metadata = _completed_metadata(document)
    saved_eligibility = metadata.get("eligibility")
    if isinstance(saved_eligibility, dict) and saved_eligibility.get("medplum_resource"):
        return DemoEligibilityOut(checkin_id=payload.checkin_id, **saved_eligibility)
    pending = metadata.get("eligibility_pending")
    result = pending if isinstance(pending, dict) else None
    try:
        if result is None:
            result = await check_eligibility()
            saved_pending = await asyncio.to_thread(
                update_document_metadata,
                doc_id=payload.checkin_id,
                metadata_patch={"eligibility_pending": result},
            )
            if not saved_pending:
                raise SponsorIntegrationError(
                    "workflow",
                    "Stedi completed, but the resumable eligibility journal could not be saved.",
                )
        fhir_document = _eligibility_document(payload.checkin_id, result)
        medplum = MedplumClient()
        await medplum.assert_synthetic_patient(patient_id)
        await medplum.validate_resources([fhir_document])
        resource = (
            await medplum.transact(
                [
                    {
                        "fullUrl": f"urn:uuid:{uuid.uuid4()}",
                        "resource": fhir_document,
                        "request": {
                            "method": "POST",
                            "url": "DocumentReference",
                            "ifNoneExist": (
                                f"identifier={_ELIGIBILITY_IDENTIFIER_SYSTEM}|{payload.checkin_id}"
                            ),
                        },
                    }
                ],
                checkin_id=payload.checkin_id,
            )
        )[0]
    except SponsorIntegrationError as exc:
        _raise_sponsor(exc)
    saved = await asyncio.to_thread(
        update_document_metadata,
        doc_id=payload.checkin_id,
        metadata_patch={
            "eligibility": {**result, "medplum_resource": resource},
            "eligibility_pending": None,
        },
    )
    if not saved:
        raise HTTPException(status_code=502, detail="Eligibility succeeded but InsForge reconstruction update failed.")
    return DemoEligibilityOut(
        checkin_id=payload.checkin_id,
        **result,
        medplum_resource=resource,
    )


@router.get(
    "/patients/{patient_id}/readiness",
    response_model=DemoReadinessOut,
)
async def readiness(
    patient_id: str,
    operator: DemoOperatorDep,
    chart: DemoChartDep,
) -> DemoReadinessOut:
    del operator, chart
    document = await asyncio.to_thread(_find_checkin_document, patient_id, completed_only=True)
    if not document:
        raise HTTPException(status_code=404, detail="No approved pre-visit reconstruction exists.")
    metadata = _completed_metadata(document)
    draft = metadata.get("draft") if isinstance(metadata.get("draft"), dict) else {}
    return DemoReadinessOut(
        checkin_id=str(metadata.get("checkin_id") or document.get("doc_id") or ""),
        patient_id=patient_id,
        approved_at=str(metadata.get("approved_at") or document.get("uploaded_at") or ""),
        clinician_name=str(metadata.get("clinician_name") or ""),
        what_changed=draft.get("proposed_changes") or [],
        clinician_verification=draft.get("clinician_verification") or [],
        unresolved_questions=draft.get("unresolved_questions") or [],
        utterances=metadata.get("evidence_utterances") or [],
        resources=metadata.get("medplum_resources") or [],
        validation_status=metadata["validation_status"],
        eligibility=metadata.get("eligibility") if isinstance(metadata.get("eligibility"), dict) else None,
    )
