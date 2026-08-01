#!/usr/bin/env python3
"""Rebuild the offline demo store at ``data/local_mock/store.json`` from the fixtures.

Only relevant with ``MEDTRACE_LOCAL_MOCK=1``. Uploaded files under
``data/local_mock/files/`` are left alone; only the JSON store is regenerated from
``mock/patient_data/*.json`` plus the built-in clinical fixtures.

Usage::

    .venv/bin/python scripts/reset_local_mock.py
"""

from __future__ import annotations

import sys

def main() -> int:
    from medtrace_agent.env import load_repo_env

    load_repo_env()

    from medtrace_agent.local_store import local_mock_enabled, reset_and_reseed

    if not local_mock_enabled():
        print(
            "MEDTRACE_LOCAL_MOCK is not enabled — nothing to reset. "
            "Set MEDTRACE_LOCAL_MOCK=1 in .env to use the offline store.",
            file=sys.stderr,
        )
        return 1

    store = reset_and_reseed()
    print(
        f"Reseeded {len(store['chart_subjects'])} patient(s) and "
        f"{len(store['documents'])} document(s) into data/local_mock/store.json"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
