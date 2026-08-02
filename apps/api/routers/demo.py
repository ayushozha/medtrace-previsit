"""Real-provider orchestration for the YC Medplum hackathon demo route."""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import json
import logging
import os
import threading
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from apps.api.demo_security import (
    DEMO_TOKEN_HEADER as _DEMO_TOKEN_HEADER,
    OPERATOR_IDENTIFIER_SYSTEM as _OPERATOR_IDENTIFIER_SYSTEM,
    DemoOperator,
    DemoOperatorDep,
    require_demo_operator as _require_demo_operator,
)
from apps.api.routers.medplum_patients import get_snapshot
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
from medtrace_agent.integrations.deepgram import (
    configuration_status as deepgram_status,
    transcribe_audio,
)
from medtrace_agent.integrations.medplum import (
    MedplumClient,
    configuration_status as medplum_status,
    patient_reference,
    validate_synthetic_patient,
)
from medtrace_agent.integrations.moss_retrieval import (
    configuration_status as moss_status,
    retrieve_context,
)
from medtrace_agent.integrations.sponsor_error import SponsorIntegrationError
from medtrace_agent.integrations.stedi import check_eligibility, configuration_status as stedi_status
from medtrace_agent.medplum import MedplumError
from medtrace_agent.medplum_repository import CODE_SYSTEM, TAG_SYSTEM, repository

router = APIRouter(prefix="/api/demo", tags=["yc-medplum-demo"])

_MAX_AUDIO_BYTES = 25 * 1024 * 1024
_CHECKIN_IDENTIFIER_SYSTEM = "https://github.com/ayushozha/medtrace-previsit/checkins"
_ELIGIBILITY_IDENTIFIER_SYSTEM = (
    "https://github.com/ayushozha/medtrace-previsit/stedi-eligibility"
)
_CHECKIN_JOURNAL_TAG = "yc-demo-checkin"
_CHECKIN_TOKEN_TTL_SECONDS = 2 * 60 * 60
_SPONSOR_RATE_LIMIT = 8
_SPONSOR_RATE_WINDOW_SECONDS = 60.0
_sponsor_calls: dict[str, deque[float]] = defaultdict(deque)
_sponsor_calls_lock = threading.Lock()

logger = logging.getLogger(__name__)


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
    return {"configured": not missing, "missing": missing}


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


async def _require_real_data_layer(
    patient_id: str,
    _operator: DemoOperatorDep,
) -> dict[str, Any]:
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
    try:
        patient = await MedplumClient().assert_synthetic_patient(patient_id)
    except SponsorIntegrationError as exc:
        _raise_sponsor(exc)
    return repository().patient_view(patient)


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
    medplum = medplum_status()
    patient_id = (os.environ.get("YC_DEMO_PATIENT_ID") or "").strip()
    safe_patient_id: str | None = None
    if patient_id and medplum["configured"]:
        try:
            patient = repository().get_patient(patient_id)
            if patient:
                validate_synthetic_patient(patient, patient_id)
                safe_patient_id = patient_id
        except (MedplumError, SponsorIntegrationError):
            logger.warning("Configured YC demo Patient failed the synthetic safety check.")
    return DemoStatusOut(
        demo_patient_id=safe_patient_id,
        deepgram=_provider_status(deepgram_status()),
        moss=_provider_status(moss_status()),
        openai=_provider_status(openai_status()),
        medplum=_provider_status(medplum),
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
    imaging_evidence: list[dict[str, Any]] | None = None,
    openai_model: str = "",
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
            "imaging_evidence_ids": [
                str(item.get("diagnostic_report_id") or "")
                for item in imaging_evidence or []
                if item.get("diagnostic_report_id")
            ],
            "imaging_evidence_digest": _imaging_evidence_digest(imaging_evidence or []),
            "openai_model": openai_model,
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


def _imaging_evidence_digest(items: list[dict[str, Any]]) -> str:
    normalized = sorted(
        (
            {
                "diagnostic_report_id": str(item.get("diagnostic_report_id") or ""),
                "imaging_study_ids": sorted(str(value) for value in item.get("imaging_study_ids") or []),
                "issued": str(item.get("issued") or "") or None,
                "summary": str(item.get("summary") or ""),
                "reviewer_id": str(item.get("reviewer_id") or "") or None,
                "reviewer_name": str(item.get("reviewer_name") or "") or None,
                "reviewed_at": str(item.get("reviewed_at") or "") or None,
                "report_version_id": str(item.get("report_version_id") or "") or None,
            }
            for item in items
        ),
        key=lambda item: item["diagnostic_report_id"],
    )
    canonical = json.dumps(
        normalized,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _verify_checkin_token(patient_id: str, payload: DemoConfirmIn) -> dict[str, Any]:
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
    return source


def _validate_reviewed_draft(payload: DemoConfirmIn) -> PrevisitDraft:
    return validate_evidence(
        PrevisitDraft.model_validate(payload.draft.model_dump(mode="json")),
        [
            item.model_dump(mode="json")
            for item in payload.utterances
            if item.speaker == payload.patient_speaker
        ],
    )


def _accepted_imaging_evidence(patient_id: str) -> list[dict[str, Any]]:
    """Return only clinician-accepted imaging reports for retrieval and provenance."""
    repo = repository()
    try:
        reports = repo.client.search(
            "DiagnosticReport",
            {
                "subject": f"Patient/{patient_id}",
                "status": "final",
                "_count": 10,
                "_sort": "-_lastUpdated",
            },
        )
    except MedplumError as exc:
        raise SponsorIntegrationError(
            "medplum",
            f"Accepted imaging context could not be loaded: {exc}",
            status_code=503 if exc.status_code in {401, 403} else 502,
        ) from exc

    accepted: list[dict[str, Any]] = []
    for report in reports:
        tags = (report.get("meta") or {}).get("tag") or []
        decision, _ = repo.report_review(report)
        reviewer = repo.report_reviewer(report)
        if decision != "accepted" or any(
            isinstance(tag, dict) and tag.get("code") == "ai-extracted-unverified"
            for tag in tags
        ) or not reviewer.get("reviewer_id") or not reviewer.get("reviewer_name"):
            continue
        view = repo.diagnostic_report_view(report)
        summary = "\n".join(
            str(view.get(key) or "").strip()
            for key in ("summary", "findings", "impression", "recommendation")
            if str(view.get(key) or "").strip()
        )
        report_id = str(report.get("id") or "")
        if not report_id or not summary:
            continue
        accepted.append(
            {
                "diagnostic_report_id": report_id,
                "imaging_study_ids": [
                    str(item.get("reference") or "").removeprefix("ImagingStudy/")
                    for item in report.get("imagingStudy") or []
                    if isinstance(item, dict) and item.get("reference")
                ],
                "issued": str(report.get("issued") or "") or None,
                "summary": summary[:4_000],
                "reviewer_id": reviewer["reviewer_id"],
                "reviewer_name": reviewer["reviewer_name"],
                "reviewed_at": reviewer.get("reviewed_at"),
                "report_version_id": str((report.get("meta") or {}).get("versionId") or "") or None,
            }
        )
    return accepted


def _snapshot_documents(
    snapshot: dict[str, Any],
    utterances: list[dict[str, Any]],
    imaging_evidence: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
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
    docs.extend(
        {
            "id": f"diagnostic-report-{item['diagnostic_report_id']}",
            "text": str(item["summary"]),
            "metadata": {
                "patient_id": patient_id,
                "source": "accepted_imaging_report",
                "fhir_resource_id": str(item["diagnostic_report_id"]),
            },
        }
        for item in imaging_evidence or []
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
        imaging_evidence = await asyncio.to_thread(_accepted_imaging_evidence, patient_id)
        moss = await retrieve_context(
            patient_id=patient_id,
            checkin_id=checkin_id,
            documents=_snapshot_documents(
                snapshot.model_dump(mode="json"), deepgram["utterances"], imaging_evidence
            ),
            query=(
                "What changed in medication adherence, allergies, worsening biometrics, and unresolved "
                "follow-up questions during today's pre-visit check-in?"
            ),
        )
        draft, openai_response_id, openai_model = await create_previsit_draft(
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
            imaging_evidence=imaging_evidence,
            openai_model=openai_model,
        ),
        utterances=deepgram["utterances"],
        moss=moss,
        imaging_evidence=imaging_evidence,
        openai_model=openai_model,
        draft=draft.model_dump(mode="json"),
    )


def _checkin_identifier(checkin_id: str, suffix: str) -> dict[str, str]:
    return {"system": _CHECKIN_IDENTIFIER_SYSTEM, "value": f"{checkin_id}:{suffix}"}


def _resource_urn(checkin_id: str, suffix: str) -> str:
    return f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, f'{_CHECKIN_IDENTIFIER_SYSTEM}:{checkin_id}:{suffix}')}"


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
    patient_id: str,
    payload: DemoConfirmIn,
    operator: DemoOperator,
    note_text: str,
    imaging_evidence: list[dict[str, Any]] | None = None,
    openai_model: str = "",
) -> list[tuple[str, dict[str, Any]]]:
    now = datetime.now(timezone.utc).isoformat()
    patient = patient_reference(patient_id)
    encounter_urn = _resource_urn(payload.checkin_id, "encounter")
    journal_urn = _resource_urn(payload.checkin_id, "reconstruction")
    reconstruction = {
        "yc_demo_checkin": True,
        "checkin_id": payload.checkin_id,
        "deepgram_request_id": payload.deepgram_request_id,
        "openai_response_id": payload.openai_response_id,
        "openai_model": openai_model,
        "patient_speaker": payload.patient_speaker,
        "operator_id": operator.operator_id,
        "clinician_name": operator.display_name,
        "evidence_utterances": _approved_utterances(payload),
        "transcript_utterances": [item.model_dump(mode="json") for item in payload.utterances],
        "imaging_evidence": imaging_evidence or [],
        "draft": payload.draft.model_dump(mode="json"),
        "review_audit": _review_audit(payload),
        "approved_at": now,
        "workflow_state": "approval_recorded",
        "validation_status": "pending",
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
                "type": [{"text": "AI-assisted pre-visit check-in"}],
                "subject": {"reference": patient},
                "period": {"start": now, "end": now},
                "reasonCode": [{"text": "Clinician-reviewed pre-visit check-in"}],
            },
        ),
        (
            _resource_urn(payload.checkin_id, "questionnaire-response"),
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
            journal_urn,
            {
                "resourceType": "DocumentReference",
                "identifier": [_checkin_identifier(payload.checkin_id, "reconstruction")],
                "meta": {
                    "tag": [
                        {"system": TAG_SYSTEM, "code": _CHECKIN_JOURNAL_TAG}
                    ]
                },
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
        (
            _resource_urn(payload.checkin_id, "zep-projection"),
            {
                "resourceType": "Task",
                "identifier": [_checkin_identifier(payload.checkin_id, "zep-projection")],
                "status": "requested",
                "intent": "order",
                "code": {
                    "coding": [
                        {
                            "system": CODE_SYSTEM,
                            "code": "zep-demo-projection",
                            "display": "zep-demo-projection",
                        }
                    ],
                    "text": "zep-demo-projection",
                },
                "focus": {"reference": journal_urn},
                "for": {"reference": patient},
                "authoredOn": now,
                "lastModified": now,
                "input": [{"type": {"text": "note-text"}, "valueString": note_text}],
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
        resources.append((_resource_urn(payload.checkin_id, f"change-{index + 1}"), resource))
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
    params: dict[str, Any] = {
        "subject": f"Patient/{patient_id}",
        "_count": 100,
        "_sort": "-date",
    }
    if checkin_id:
        params["identifier"] = (
            f"{_CHECKIN_IDENTIFIER_SYSTEM}|{checkin_id}:reconstruction"
        )
    try:
        documents = repository().client.search("DocumentReference", params)
    except MedplumError as exc:
        raise HTTPException(
            status_code=503 if exc.status_code in {401, 403} else 502,
            detail=f"Medplum workflow journal read failed: {exc}",
        ) from exc

    matches: list[dict[str, Any]] = []
    for resource in documents:
        subject = resource.get("subject") if isinstance(resource.get("subject"), dict) else {}
        tags = (resource.get("meta") or {}).get("tag") or []
        identifiers = resource.get("identifier") or []
        if subject.get("reference") != f"Patient/{patient_id}" or not any(
            isinstance(tag, dict)
            and tag.get("system") == TAG_SYSTEM
            and tag.get("code") == _CHECKIN_JOURNAL_TAG
            for tag in tags
        ):
            continue
        if checkin_id and not any(
            isinstance(identifier, dict)
            and identifier.get("system") == _CHECKIN_IDENTIFIER_SYSTEM
            and identifier.get("value") == f"{checkin_id}:reconstruction"
            for identifier in identifiers
        ):
            continue
        metadata = _decode_document_json(resource)
        if not metadata.get("yc_demo_checkin"):
            continue
        if checkin_id and str(metadata.get("checkin_id")) != checkin_id:
            continue
        if completed_only and metadata.get("workflow_state") != "complete":
            continue
        matches.append(
            {
                "doc_id": str(resource.get("id") or ""),
                "uploaded_at": str(
                    resource.get("date") or (resource.get("meta") or {}).get("lastUpdated") or ""
                ),
                "metadata": metadata,
                "_resource": resource,
            }
        )
    return max(
        matches,
        key=lambda row: str(
            ((row.get("metadata") or {}).get("approved_at") if isinstance(row.get("metadata"), dict) else "")
            or row.get("uploaded_at")
            or ""
        ),
        default=None,
    )


def _decode_document_json(resource: dict[str, Any]) -> dict[str, Any]:
    for content in resource.get("content") or []:
        attachment = content.get("attachment") if isinstance(content, dict) else None
        if not isinstance(attachment, dict) or attachment.get("contentType") != "application/json":
            continue
        encoded = attachment.get("data")
        if not isinstance(encoded, str):
            continue
        try:
            decoded = json.loads(base64.b64decode(encoded, validate=True))
        except (binascii.Error, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=502, detail="The Medplum workflow journal is unreadable.") from exc
        if isinstance(decoded, dict):
            return decoded
    return {}


def _update_document_metadata(
    patient_id: str,
    checkin_id: str,
    metadata_patch: dict[str, Any],
) -> dict[str, Any] | None:
    row = _find_checkin_document(patient_id, checkin_id)
    if not row:
        return None
    resource = row.get("_resource")
    if not isinstance(resource, dict):
        return None
    metadata = dict(row.get("metadata") or {})
    metadata.update(metadata_patch)
    retained = []
    for content in resource.get("content") or []:
        attachment = content.get("attachment") if isinstance(content, dict) else None
        if not isinstance(attachment, dict) or attachment.get("contentType") != "application/json":
            retained.append(content)
    updated = {
        **resource,
        "content": [
            {
                "attachment": {
                    "contentType": "application/json",
                    "title": f"Pre-visit reconstruction {checkin_id}",
                    "data": base64.b64encode(
                        json.dumps(metadata, ensure_ascii=False).encode("utf-8")
                    ).decode("ascii"),
                }
            },
            *retained,
        ],
    }
    try:
        saved = repository().client.update(updated)
    except MedplumError as exc:
        raise HTTPException(
            status_code=503 if exc.status_code in {401, 403} else 502,
            detail=f"Medplum workflow journal update failed: {exc}",
        ) from exc
    return {
        "doc_id": str(saved.get("id") or ""),
        "uploaded_at": str(saved.get("date") or (saved.get("meta") or {}).get("lastUpdated") or ""),
        "metadata": metadata,
        "_resource": saved,
    }


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
    )


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
    del chart
    _enforce_sponsor_rate_limit(operator)
    if not payload.approved:
        raise HTTPException(status_code=409, detail="No FHIR write occurs until the clinician approves.")
    try:
        token_context = _verify_checkin_token(patient_id, payload)
        draft = _validate_reviewed_draft(payload)
        payload.draft = payload.draft.model_validate(draft.model_dump(mode="json"))
    except SponsorIntegrationError as exc:
        _raise_sponsor(exc)

    existing = await asyncio.to_thread(_find_checkin_document, patient_id, payload.checkin_id)
    if existing:
        metadata = existing.get("metadata") if isinstance(existing.get("metadata"), dict) else {}
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
        if metadata.get("workflow_state") == "complete":
            return _saved_confirmation(existing, payload, operator)
    else:
        metadata = {}

    signed_imaging_ids = {
        str(value)
        for value in token_context.get("imaging_evidence_ids") or []
        if value
    }
    imaging_evidence: list[dict[str, Any]] = []
    if signed_imaging_ids:
        try:
            imaging_evidence = [
                item
                for item in await asyncio.to_thread(_accepted_imaging_evidence, patient_id)
                if item["diagnostic_report_id"] in signed_imaging_ids
            ]
            if {item["diagnostic_report_id"] for item in imaging_evidence} != signed_imaging_ids:
                raise SponsorIntegrationError(
                    "workflow",
                    "Accepted imaging provenance changed before clinician approval.",
                    status_code=409,
                )
            if not hmac.compare_digest(
                str(token_context.get("imaging_evidence_digest") or ""),
                _imaging_evidence_digest(imaging_evidence),
            ):
                raise SponsorIntegrationError(
                    "workflow",
                    "Accepted imaging content changed after the OpenAI draft was created.",
                    status_code=409,
                )
        except SponsorIntegrationError as exc:
            _raise_sponsor(exc)

    note_text = _note_text(payload, operator)
    validations = metadata.get("validations") if isinstance(metadata.get("validations"), list) else []
    resources = (
        metadata.get("medplum_resources")
        if isinstance(metadata.get("medplum_resources"), list)
        else []
    )
    if not resources:
        try:
            fhir_pairs = _fhir_resources(
                patient_id,
                payload,
                operator,
                note_text,
                imaging_evidence=imaging_evidence,
                openai_model=str(token_context.get("openai_model") or ""),
            )
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
    completed = await asyncio.to_thread(
        _update_document_metadata,
        patient_id,
        payload.checkin_id,
        {
            "validation_status": "passed",
            "validations": validations,
            "medplum_resources": resources,
            "zep_projection_status": "requested",
            "workflow_state": "complete",
        },
    )
    if not completed:
        raise HTTPException(
            status_code=502,
            detail=(
                "Medplum committed the conditional FHIR transaction, but its workflow journal "
                "could not be finalized. Retry with the same check-in ID."
            ),
        )
    return DemoConfirmOut(
        checkin_id=payload.checkin_id,
        approved=True,
        validation_status="passed",
        validations=validations,
        resources=resources,
        document_id=str(completed.get("doc_id") or ""),
    )


def _eligibility_document(
    patient_id: str, checkin_id: str, result: dict[str, Any]
) -> dict[str, Any]:
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
        "meta": {"tag": [{"system": TAG_SYSTEM, "code": "yc-demo-eligibility"}]},
        "status": "current",
        "type": {"text": "Stedi test-mode eligibility response"},
        "subject": {"reference": patient_reference(patient_id)},
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


def _find_eligibility_document(patient_id: str, checkin_id: str) -> dict[str, Any] | None:
    try:
        rows = repository().client.search(
            "DocumentReference",
            {
                "subject": f"Patient/{patient_id}",
                "identifier": f"{_ELIGIBILITY_IDENTIFIER_SYSTEM}|{checkin_id}",
                "_count": 2,
            },
        )
    except MedplumError as exc:
        raise HTTPException(
            status_code=503 if exc.status_code in {401, 403} else 502,
            detail=f"Medplum eligibility read failed: {exc}",
        ) from exc
    for resource in rows:
        subject = resource.get("subject") if isinstance(resource.get("subject"), dict) else {}
        if subject.get("reference") != f"Patient/{patient_id}":
            continue
        if any(
            isinstance(identifier, dict)
            and identifier.get("system") == _ELIGIBILITY_IDENTIFIER_SYSTEM
            and identifier.get("value") == checkin_id
            for identifier in resource.get("identifier") or []
        ):
            return resource
    return None


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
    existing_resource = await asyncio.to_thread(
        _find_eligibility_document, patient_id, payload.checkin_id
    )
    resource: dict[str, Any] | None = None
    if existing_resource:
        stored = _decode_document_json(existing_resource)
        if stored:
            result = stored
            resource = MedplumClient.resource_result(
                existing_resource, checkin_id=payload.checkin_id
            )
    try:
        if result is None:
            result = await check_eligibility()
            saved_pending = await asyncio.to_thread(
                _update_document_metadata,
                patient_id,
                payload.checkin_id,
                {"eligibility_pending": result},
            )
            if not saved_pending:
                raise SponsorIntegrationError(
                    "workflow",
                    "Stedi completed, but the resumable eligibility journal could not be saved.",
                )
        if resource is None:
            fhir_document = _eligibility_document(patient_id, payload.checkin_id, result)
            medplum = MedplumClient()
            await medplum.assert_synthetic_patient(patient_id)
            await medplum.validate_resources([fhir_document])
            resource = (
                await medplum.transact(
                    [
                        {
                            "fullUrl": _resource_urn(payload.checkin_id, "eligibility"),
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
        _update_document_metadata,
        patient_id,
        payload.checkin_id,
        {
            "eligibility": {**result, "medplum_resource": resource},
            "eligibility_pending": None,
        },
    )
    if not saved:
        raise HTTPException(
            status_code=502,
            detail="Eligibility succeeded but the Medplum reconstruction update failed.",
        )
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
    checkin_id = str(metadata.get("checkin_id") or document.get("doc_id") or "")
    saved_eligibility = metadata.get("eligibility")
    return DemoReadinessOut(
        checkin_id=checkin_id,
        patient_id=patient_id,
        approved_at=str(metadata.get("approved_at") or document.get("uploaded_at") or ""),
        clinician_name=str(metadata.get("clinician_name") or ""),
        what_changed=draft.get("proposed_changes") or [],
        clinician_verification=draft.get("clinician_verification") or [],
        unresolved_questions=draft.get("unresolved_questions") or [],
        utterances=metadata.get("transcript_utterances") or metadata.get("evidence_utterances") or [],
        resources=metadata.get("medplum_resources") or [],
        validations=metadata.get("validations") or [],
        imaging_evidence=metadata.get("imaging_evidence") or [],
        deepgram_request_id=str(metadata.get("deepgram_request_id") or ""),
        openai_response_id=str(metadata.get("openai_response_id") or ""),
        openai_model=str(metadata.get("openai_model") or "unknown"),
        document_id=str(document.get("doc_id") or ""),
        validation_status=metadata["validation_status"],
        eligibility=(
            DemoEligibilityOut(checkin_id=checkin_id, **saved_eligibility)
            if isinstance(saved_eligibility, dict) and saved_eligibility.get("medplum_resource")
            else None
        ),
    )
