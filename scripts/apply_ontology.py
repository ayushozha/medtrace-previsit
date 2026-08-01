#!/usr/bin/env python3
"""Register the clinical ontology with Zep (project-wide) — reads repo ``.env``.

``apps/api`` applies this automatically at startup; use this script to re-apply after
editing ``medtrace_agent.ontology.clinical`` without restarting the API.

Usage::

    .venv/bin/python scripts/apply_ontology.py
    .venv/bin/python scripts/apply_ontology.py --user-id <zep_user_id>   # scope to one user
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

def main() -> int:
    parser = argparse.ArgumentParser(description="Apply the clinical ontology to Zep.")
    parser.add_argument(
        "--user-id",
        default=None,
        help="Scope registration to a single Zep user instead of the whole project.",
    )
    args = parser.parse_args()

    from medtrace_agent.env import load_repo_env

    load_repo_env()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if not (os.environ.get("ZEP_API_KEY") or "").strip():
        print("Set ZEP_API_KEY in .env", file=sys.stderr)
        return 1

    from medtrace_agent.ontology import apply_clinical_ontology

    try:
        apply_clinical_ontology(args.user_id, scope_to_user=bool(args.user_id))
    except Exception as exc:  # noqa: BLE001 — surface the Zep error verbatim
        print(f"Ontology registration failed: {exc}", file=sys.stderr)
        return 1

    scope = f"user {args.user_id}" if args.user_id else "project-wide"
    print(f"Clinical ontology applied ({scope}). Check the Zep dashboard → ontology.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
