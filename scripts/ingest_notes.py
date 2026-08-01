#!/usr/bin/env python3
"""Bulk-ingest ``data/radiology_note/*.txt`` and ``data/session_note/*.txt`` into a patient.

The API only accepts uploaded bytes; this covers the on-disk note folders. Each file is
chunked into the patient's Zep graph and registered in InsForge (or the local mock) so it
shows up in the web document library.

Usage::

    .venv/bin/python scripts/ingest_notes.py --user-id <zep_user_id>
    .venv/bin/python scripts/ingest_notes.py --user-id <id> --source radiology_note
    .venv/bin/python scripts/ingest_notes.py --user-id <id> --list
"""

from __future__ import annotations

import argparse
import sys
import uuid

SOURCES = ("radiology_note", "session_note")
# documents.document_kind is a closed set; session notes register as conversation_note.
_DOC_KIND = {"radiology_note": "radiology_note", "session_note": "conversation_note"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest on-disk .txt notes into a patient graph.")
    parser.add_argument("--user-id", help="Zep user id (patient) to ingest into.")
    parser.add_argument(
        "--source",
        choices=SOURCES,
        action="append",
        help="Note folder to ingest; repeatable. Defaults to both.",
    )
    parser.add_argument(
        "--display-name",
        default=None,
        help="Patient display name used when creating the InsForge chart row.",
    )
    parser.add_argument("--list", action="store_true", help="List discovered files and exit.")
    parser.add_argument(
        "--no-registry",
        action="store_true",
        help="Ingest into Zep only; skip the InsForge/local document registry.",
    )
    args = parser.parse_args()

    from medtrace_agent.env import load_repo_env

    load_repo_env()

    from medtrace_agent.ingest.documents import (
        ingest_txt_path_to_patient_graph,
        list_txt_files_in_note_folder,
    )
    from medtrace_agent.insforge_api import (
        insforge_persistence_enabled,
        persist_ingest_with_upload,
    )

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

    display_name = args.display_name or args.user_id
    register = not args.no_registry and insforge_persistence_enabled()
    if not args.no_registry and not register:
        print(
            "Note: document registry is disabled (no InsForge creds and MEDTRACE_LOCAL_MOCK "
            "is off) — ingesting into Zep only.",
            file=sys.stderr,
        )

    failures = 0
    for source in sources:
        files = list_txt_files_in_note_folder(source)
        if not files:
            print(f"{source}: no .txt files found.")
            continue
        for path in files:
            doc_id = uuid.uuid4().hex
            try:
                episode_ids = ingest_txt_path_to_patient_graph(
                    args.user_id,
                    path,
                    note_source=source,
                    doc_id=doc_id,
                )
            except Exception as exc:  # noqa: BLE001 — report and keep going
                print(f"{source}/{path.name}: FAILED — {exc}", file=sys.stderr)
                failures += 1
                continue

            if register:
                try:
                    persist_ingest_with_upload(
                        file_bytes=path.read_bytes(),
                        filename=path.name,
                        doc_id=doc_id,
                        zep_user_id=args.user_id,
                        patient_display_name=display_name,
                        document_kind=_DOC_KIND[source],
                        extract_mode=None,
                        episode_count=len(episode_ids),
                    )
                except Exception as exc:  # noqa: BLE001 — Zep write already succeeded
                    print(f"{source}/{path.name}: registry write failed — {exc}", file=sys.stderr)
                    failures += 1

            print(f"{source}/{path.name}: {len(episode_ids)} episode(s), doc_id={doc_id[:8]}…")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
