"""OpenAI structured extraction for evidence-linked pre-visit changes."""

from __future__ import annotations

import json
import os
from typing import Any, Literal
from urllib.parse import urlparse

from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict

from medtrace_agent.integrations.sponsor_error import SponsorIntegrationError


class ProposedChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["medication_adherence", "allergy_confirmation", "follow_up"]
    title: str
    clinical_subject: str
    proposed_value: str
    evidence_utterance_id: str
    evidence_quote: str
    clinician_note: str


class PrevisitDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str
    proposed_changes: list[ProposedChange]
    unresolved_questions: list[str]
    clinician_verification: list[str]
    recommended_visit: bool
    recommended_service: str


def _official_openai_base_url(value: str) -> bool:
    parsed = urlparse(value)
    try:
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and parsed.hostname == "api.openai.com"
        and parsed.username is None
        and parsed.password is None
        and port in {None, 443}
        and parsed.path.rstrip("/") in {"", "/v1"}
        and not parsed.query
        and not parsed.fragment
    )


def configuration_status() -> dict[str, object]:
    required = ("OPENAI_API_KEY", "OPENAI_MODEL")
    missing = [name for name in required if not (os.environ.get(name) or "").strip()]
    base_url = (os.environ.get("OPENAI_BASE_URL") or "").strip()
    if base_url and not _official_openai_base_url(base_url):
        missing.append("OPENAI_BASE_URL must use the official HTTPS api.openai.com endpoint")
    return {"configured": not missing, "missing": missing}


def _required(name: str) -> str:
    value = (os.environ.get(name) or "").strip()
    if not value:
        raise SponsorIntegrationError(
            "openai", f"{name} is required for the real OpenAI draft path.", status_code=503
        )
    return value


def validate_evidence(draft: PrevisitDraft, utterances: list[dict[str, Any]]) -> PrevisitDraft:
    """Require every model-proposed claim to quote its cited Deepgram utterance."""
    by_id = {str(item.get("id")): str(item.get("text") or "") for item in utterances}
    for change in draft.proposed_changes:
        source = by_id.get(change.evidence_utterance_id)
        if source is None:
            raise SponsorIntegrationError(
                "openai", f"Draft cited unknown utterance {change.evidence_utterance_id}.", status_code=502
            )
        quote = change.evidence_quote.strip()
        offset = source.lower().find(quote.lower())
        if not quote or offset < 0:
            raise SponsorIntegrationError(
                "openai",
                f"Draft evidence for {change.title} is not an exact Deepgram transcript quote.",
                status_code=502,
            )
        change.evidence_quote = source[offset : offset + len(quote)]
    return draft


async def create_previsit_draft(
    *,
    utterances: list[dict[str, Any]],
    retrieval: list[dict[str, Any]],
) -> tuple[PrevisitDraft, str, str]:
    """Use OpenAI Responses structured outputs; no clinical write occurs here."""
    kwargs: dict[str, Any] = {"api_key": _required("OPENAI_API_KEY")}
    base_url = (os.environ.get("OPENAI_BASE_URL") or "").strip()
    if base_url:
        if not _official_openai_base_url(base_url):
            raise SponsorIntegrationError(
                "openai",
                "OPENAI_BASE_URL must use the official HTTPS api.openai.com endpoint.",
                status_code=503,
            )
        kwargs["base_url"] = base_url
    client = AsyncOpenAI(**kwargs)
    transcript = [
        {
            "id": item.get("id"),
            "speaker": item.get("speaker"),
            "start": item.get("start"),
            "end": item.get("end"),
            "text": item.get("text"),
        }
        for item in utterances
    ]
    evidence = [{"id": item.get("id"), "text": item.get("text")} for item in retrieval]
    instructions = (
        "You are drafting a non-diagnostic pre-visit reconciliation for clinician review. "
        "The supplied utterances are from the patient speaker only. Extract only changes explicitly supported "
        "by those Deepgram utterances. Each proposed change must cite "
        "one utterance ID and an exact contiguous quote from that utterance. Use chart retrieval only as context, "
        "never as proof of what the patient said today. Capture medication adherence, allergy confirmation, and "
        "unresolved follow-up as evidence-linked proposed changes when stated. Set clinical_subject to the exact "
        "medication, allergen, or follow-up service/topic; do not use a workflow label. Do not diagnose, prescribe, "
        "or claim that any write has occurred. "
        "Use an empty list or empty string when evidence is absent."
    )
    model = _required("OPENAI_MODEL")
    try:
        response = await client.responses.parse(
            model=model,
            store=False,
            input=[
                {"role": "system", "content": instructions},
                {
                    "role": "user",
                    "content": json.dumps({"deepgram_utterances": transcript, "moss_context": evidence}),
                },
            ],
            text_format=PrevisitDraft,
        )
    except Exception as exc:
        raise SponsorIntegrationError("openai", f"OpenAI draft generation failed: {exc}") from exc
    draft = response.output_parsed
    if draft is None:
        raise SponsorIntegrationError("openai", "OpenAI returned no structured pre-visit draft.")
    return validate_evidence(draft, utterances), str(response.id), model
