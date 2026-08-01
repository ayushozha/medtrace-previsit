"""Canonical Medplum Communication threads with Zep projection."""

from __future__ import annotations

import uuid
from functools import lru_cache

from fastapi import APIRouter, HTTPException, status
from langgraph.checkpoint.memory import MemorySaver

from apps.api.dependencies import RequireMedplumDep
from apps.api.routers.medplum_common import document_catalog, raise_medplum_http
from apps.api.schemas import ChatMessageOut, ChatThreadOut, CreateThreadIn, SendMessageIn, SendMessageOut
from medtrace_agent.fireworks_config import fireworks_chat_model
from medtrace_agent.medplum import MedplumError
from medtrace_agent.medplum_repository import ZEP_USER_SYSTEM, identifier_value, repository
from medtrace_agent.zep.memory import append_turn, ensure_session, ensure_user, fetch_thread_context


router = APIRouter(tags=["threads"])


@lru_cache(maxsize=1)
def _shared_checkpointer() -> MemorySaver:
    return MemorySaver()


def _patient_or_404(patient_id: str) -> dict:
    try:
        patient = repository().get_patient(patient_id)
    except MedplumError as exc:
        raise_medplum_http(exc)
    if not patient:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found.")
    return patient


def _patient_name(patient: dict) -> str:
    names = patient.get("name") or []
    return str(names[0].get("text") or "Patient") if names and isinstance(names[0], dict) else "Patient"


@router.get("/api/patients/{patient_id}/threads", response_model=list[ChatThreadOut], dependencies=[RequireMedplumDep])
def list_threads(patient_id: str) -> list[ChatThreadOut]:
    _patient_or_404(patient_id)
    try:
        repo = repository()
        return [ChatThreadOut.model_validate(repo.thread_view(row)) for row in repo.list_threads(patient_id)]
    except MedplumError as exc:
        raise_medplum_http(exc)


@router.post(
    "/api/patients/{patient_id}/threads",
    response_model=ChatThreadOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[RequireMedplumDep],
)
def create_thread(patient_id: str, body: CreateThreadIn) -> ChatThreadOut:
    patient = _patient_or_404(patient_id)
    zep_thread_id = f"react-{patient_id[:8]}-{uuid.uuid4().hex[:10]}"
    try:
        repo = repository()
        thread = repo.create_thread(patient_id=patient_id, zep_thread_id=zep_thread_id, title=body.title)
    except MedplumError as exc:
        raise_medplum_http(exc)
    zep_user_id = identifier_value(patient, ZEP_USER_SYSTEM) or ""
    try:
        ensure_user(zep_user_id, _patient_name(patient))
        ensure_session(zep_thread_id, zep_user_id)
    except Exception:
        # Medplum remains canonical; the projection worker will retry after Zep recovers.
        pass
    return ChatThreadOut.model_validate(repo.thread_view(thread))


@router.get("/api/threads/{zep_thread_id}/messages", response_model=list[ChatMessageOut], dependencies=[RequireMedplumDep])
def list_messages(zep_thread_id: str, lastn: int = 50) -> list[ChatMessageOut]:
    repo = repository()
    try:
        thread = repo.thread_by_zep(zep_thread_id)
        if not thread:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Thread not found.")
        return [ChatMessageOut.model_validate(repo.message_view(row)) for row in repo.list_messages(str(thread["id"]), limit=lastn)]
    except MedplumError as exc:
        raise_medplum_http(exc)


@router.post("/api/threads/{zep_thread_id}/messages", response_model=SendMessageOut, dependencies=[RequireMedplumDep])
def send_message(zep_thread_id: str, body: SendMessageIn) -> SendMessageOut:
    text = body.user_input.strip()
    if not text:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="user_input is required.")
    request_id = body.request_id or uuid.uuid4().hex
    repo = repository()
    try:
        thread = repo.thread_by_zep(zep_thread_id)
        if not thread:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Thread not found.")
        patient_id = str((thread.get("subject") or {}).get("reference") or "").removeprefix("Patient/")
        patient = repo.get_patient(patient_id)
        if not patient:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found.")

        existing_user = repo.message_by_request(request_id, "user")
        existing_assistant = repo.message_by_request(request_id, "assistant")
        if existing_user and existing_assistant:
            return SendMessageOut(
                user=ChatMessageOut.model_validate(repo.message_view(existing_user)),
                assistant=ChatMessageOut.model_validate(repo.message_view(existing_assistant)),
            )

        prior_messages = [repo.message_view(row) for row in repo.list_messages(str(thread["id"]), limit=100)]
        user_message = existing_user or repo.create_message(
            thread=thread,
            patient_id=patient_id,
            role="user",
            content=text,
            request_id=request_id,
        )
        zep_user_id = identifier_value(patient, ZEP_USER_SYSTEM) or ""
        zep_context = ""
        try:
            ensure_user(zep_user_id, _patient_name(patient))
            ensure_session(zep_thread_id, zep_user_id)
            zep_context, _ = fetch_thread_context(zep_thread_id)
        except Exception:
            pass
        resources = repo.clinical_resources(patient_id)
        docs = repo.list_documents(patient_id, resources)
        fhir_context = {
            "conditions": repo.condition_views(resources),
            "medications": repo.medication_views(resources),
            "allergies": repo.allergy_views(resources),
            "labs": repo.lab_views(resources),
            "timeline": repo.timeline_views(resources),
            "approved_previsit_reconstructions": [
                item
                for item in repo.json_document_payloads(
                    resources,
                    document_type="Clinician-approved pre-visit reconstruction",
                )
                if (item.get("payload") or {}).get("workflow_state") == "complete"
            ],
        }
        combined_context = f"{zep_context}\n\n## Canonical FHIR chart\n{fhir_context}".strip()

        if body.deep:
            from medtrace_agent.agents.deep_clinical import run_clinical_deep_agent_turn

            assistant_text = run_clinical_deep_agent_turn(
                user_id=zep_user_id,
                thread_id=zep_thread_id,
                model_name=fireworks_chat_model(),
                user_input=text,
                document_catalog=document_catalog(docs) or None,
                canonical_context=combined_context,
                checkpointer=_shared_checkpointer(),
            )
        else:
            from medtrace_agent.agents.rag_chat import chat_with_memory

            assistant_text = chat_with_memory(
                user_input=text,
                user_display_name=_patient_name(patient),
                zep_context=combined_context,
                thread_messages=prior_messages,
                model_name=fireworks_chat_model(),
                document_catalog=document_catalog(docs) or None,
            )
        assistant_message = repo.create_message(
            thread=thread,
            patient_id=patient_id,
            role="assistant",
            content=assistant_text,
            request_id=request_id,
        )
        task = repo.create_zep_projection_task(
            patient_id=patient_id,
            thread_id=str(thread["id"]),
            user_message_id=str(user_message["id"]),
            assistant_message_id=str(assistant_message["id"]),
            request_id=request_id,
        )
        try:
            append_turn(zep_thread_id, _patient_name(patient), text, assistant_text)
            repo.complete_projection_task(task)
        except Exception:
            pass
        return SendMessageOut(
            user=ChatMessageOut.model_validate(repo.message_view(user_message)),
            assistant=ChatMessageOut.model_validate(repo.message_view(assistant_message)),
        )
    except HTTPException:
        raise
    except MedplumError as exc:
        raise_medplum_http(exc)
    except Exception as exc:
        # The user message is already canonical in Medplum when generation fails.
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Assistant generation failed; your message was preserved. Retry with the same request_id.",
        ) from exc
