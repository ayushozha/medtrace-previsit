#!/usr/bin/env python3
"""Import on-disk synthetic notes into canonical Medplum, then project to Zep."""

from __future__ import annotations

import argparse
import sys
import uuid

from medtrace_agent.env import load_repo_env


SOURCES = ("radiology_note", "session_note")
DOCUMENT_KIND = {"radiology_note": "radiology_note", "session_note": "conversation_note"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Import synthetic .txt notes into Medplum and Zep.")
    parser.add_argument("--user-id", help="Stable Patient Zep identifier.")
    parser.add_argument("--source", choices=SOURCES, action="append", help="Repeatable; defaults to both folders.")
    parser.add_argument("--list", action="store_true", help="List discovered files and exit.")
    args = parser.parse_args()
    load_repo_env()

    from medtrace_agent.ingest.documents import ingest_plain_text_note_to_patient_graph, list_txt_files_in_note_folder
    from medtrace_agent.medplum import medplum_configured
    from medtrace_agent.medplum_extraction import facts_from_plain_text
    from medtrace_agent.medplum_repository import repository

    sources = args.source or list(SOURCES)
    if args.list:
        for source in sources:
            files = list_txt_files_in_note_folder(source)
            print(f"{source}: {len(files)} file(s)")
            for path in files:
                print(f"  {path.name}")
        return 0
    if not args.user_id:
        parser.error("--user-id is required (or pass --list)")
    if not medplum_configured():
        print("Medplum client credentials are required; run npm run medplum:bootstrap.", file=sys.stderr)
        return 2

    repo = repository()
    patient = repo.find_patient_by_zep(args.user_id)
    if not patient:
        print(f"No Medplum Patient has Zep identifier {args.user_id!r}.", file=sys.stderr)
        return 2

    failures = 0
    for source in sources:
        for path in list_txt_files_in_note_folder(source):
            text = path.read_text(encoding="utf-8")
            source_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"note:{args.user_id}:{source}:{path.name}"))
            task = None
            try:
                doc, task = repo.create_document(
                    patient_id=str(patient["id"]),
                    data=text.encode("utf-8"),
                    filename=path.name,
                    content_type="text/plain; charset=utf-8",
                    document_kind=DOCUMENT_KIND[source],
                    extract_mode="plain-text-patterns",
                    source_doc_id=source_id,
                )
                if task.get("status") == "completed":
                    print(f"{source}/{path.name}: already imported as DocumentReference/{doc['id']}")
                    continue
                has_text = any(
                    str((item.get("attachment") or {}).get("contentType") or "").startswith("text/plain")
                    for item in doc.get("content") or []
                    if isinstance(item, dict)
                )
                if not has_text:
                    doc, task = repo.attach_extracted_text(doc, task, text)
                repo.create_extracted_facts(
                    patient_id=str(patient["id"]),
                    doc_id=str(doc["id"]),
                    facts=facts_from_plain_text(text),
                    model_name="plain-text-patterns",
                )
                episodes = ingest_plain_text_note_to_patient_graph(
                    args.user_id,
                    text,
                    note_source=source,
                    filename=path.name,
                    doc_id=str(doc["id"]),
                )
                repo.finish_task(task, episode_count=len(episodes))
                print(f"{source}/{path.name}: {len(episodes)} Zep episode(s), DocumentReference/{doc['id']}")
            except Exception:
                failures += 1
                try:
                    if task is not None:
                        repo.finish_task(task, error="Note processing or Zep projection failed; retry is safe.")
                except Exception:
                    pass
                print(f"{source}/{path.name}: failed; canonical source is preserved when created.", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
