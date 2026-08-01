#!/usr/bin/env python
"""Poll durable Medplum Tasks and project canonical content into Zep."""

from __future__ import annotations

import argparse
import time

from medtrace_agent.env import load_repo_env
from medtrace_agent.ingest.documents import ingest_pdf_text_to_patient_graph, ingest_plain_text_note_to_patient_graph
from medtrace_agent.medplum import medplum_configured
from medtrace_agent.medplum_repository import THREAD_SYSTEM, ZEP_USER_SYSTEM, identifier_value, repository
from medtrace_agent.zep.memory import append_turn, ensure_session, ensure_user


def _input_reference(task: dict, label: str, resource_type: str) -> str | None:
    for item in task.get("input") or []:
        if not isinstance(item, dict) or (item.get("type") or {}).get("text") != label:
            continue
        reference = str((item.get("valueReference") or {}).get("reference") or "")
        prefix = f"{resource_type}/"
        if reference.startswith(prefix):
            return reference.removeprefix(prefix)
    return None


def _payload(message: dict) -> str:
    payload = message.get("payload") or []
    return str(payload[0].get("contentString") or "") if payload and isinstance(payload[0], dict) else ""


def _input_string(task: dict, label: str) -> str | None:
    for item in task.get("input") or []:
        if isinstance(item, dict) and (item.get("type") or {}).get("text") == label:
            value = str(item.get("valueString") or "").strip()
            return value or None
    return None


def process_task(task: dict) -> bool:
    repo = repository()
    code = (task.get("code") or {}).get("text")
    patient_id = str((task.get("for") or {}).get("reference") or "").removeprefix("Patient/")
    patient = repo.get_patient(patient_id)
    if not patient:
        repo.abandon_projection_task(task, reason="Patient not found for projection task.")
        return False
    zep_user_id = identifier_value(patient, ZEP_USER_SYSTEM) or ""
    if not zep_user_id:
        repo.abandon_projection_task(task, reason="Patient is missing its Zep projection identifier.")
        return False
    names = patient.get("name") or []
    patient_name = str(names[0].get("text") or zep_user_id) if names and isinstance(names[0], dict) else zep_user_id

    try:
        ensure_user(zep_user_id, patient_name)
        if code == "zep-chat-projection":
            thread_id = _input_reference(task, "thread", "Communication")
            user_id = _input_reference(task, "user-message", "Communication")
            assistant_id = str((task.get("focus") or {}).get("reference") or "").removeprefix("Communication/")
            if not thread_id or not user_id or not assistant_id:
                raise ValueError("Projection task is missing Communication references.")
            thread = repo.client.read("Communication", thread_id)
            user = repo.client.read("Communication", user_id)
            assistant = repo.client.read("Communication", assistant_id)
            zep_thread_id = identifier_value(thread, THREAD_SYSTEM)
            if not zep_thread_id:
                raise ValueError("Thread is missing its Zep identifier.")
            ensure_session(zep_thread_id, zep_user_id)
            append_turn(zep_thread_id, patient_name, _payload(user), _payload(assistant))
            repo.complete_projection_task(task)
            return True

        if code == "document-processing":
            binary_id = _input_reference(task, "extracted-text", "Binary")
            doc_id = str((task.get("focus") or {}).get("reference") or "").removeprefix("DocumentReference/")
            if not binary_id or not doc_id:
                repo.abandon_projection_task(task, reason="No extracted text is available for Zep projection.")
                return False
            text = repo.client.read_binary(binary_id).decode("utf-8")
            doc = repo.client.read("DocumentReference", doc_id)
            content = doc.get("content") or []
            filename = str(((content[0].get("attachment") or {}).get("title")) if content else "document")
            kind = str((doc.get("type") or {}).get("text") or "clinical_pdf")
            if kind == "clinical_pdf":
                episodes = ingest_pdf_text_to_patient_graph(zep_user_id, text, filename=filename, doc_id=doc_id)
            else:
                episodes = ingest_plain_text_note_to_patient_graph(
                    zep_user_id,
                    text,
                    note_source="radiology_note" if kind == "radiology_note" else "session_note",
                    filename=filename,
                    doc_id=doc_id,
                )
            repo.complete_projection_task(task, episode_count=len(episodes))
            return True

        if code == "zep-demo-projection":
            note_text = _input_string(task, "note-text")
            doc_id = str((task.get("focus") or {}).get("reference") or "").removeprefix("DocumentReference/")
            if not note_text or not doc_id:
                repo.abandon_projection_task(task, reason="Demo projection task is missing its note or journal reference.")
                return False
            episodes = ingest_plain_text_note_to_patient_graph(
                zep_user_id,
                note_text,
                note_source="session_note",
                filename=f"previsit-{doc_id}.txt",
                doc_id=doc_id,
                extra_metadata={"medplum_validated": True},
            )
            repo.complete_projection_task(task, episode_count=len(episodes))
            return True
    except Exception:
        repo.complete_projection_task(task, error="Zep projection failed; the worker will retry.")
        return False
    return False


def run_once() -> tuple[int, int]:
    tasks = repository().pending_projection_tasks()
    completed = sum(1 for task in tasks if process_task(task))
    return completed, len(tasks)


def main() -> None:
    load_repo_env()
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=float, default=10.0)
    args = parser.parse_args()
    while True:
        if medplum_configured():
            try:
                completed, total = run_once()
                if total:
                    print(f"Medplum→Zep projection: completed {completed}/{total} task(s).")
            except Exception:
                print("Medplum→Zep projection unavailable; retrying on the next poll.")
        if args.once:
            return
        time.sleep(max(1.0, args.interval))


if __name__ == "__main__":
    main()
