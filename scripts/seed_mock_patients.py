"""Seed all mock/patient_data/*.json patients into InsForge via the FastAPI service.

Usage::

    python scripts/seed_mock_patients.py [--api http://127.0.0.1:8001] [--force]

By default existing patients (matched by ``zep_user_id``) are skipped.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

from medtrace_agent.patient_json import derive_age, derive_primary_doctor

REPO_ROOT = Path(__file__).resolve().parents[1]
MOCK_DIR = REPO_ROOT / "mock" / "patient_data"


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed mock patients into InsForge via the FastAPI.")
    parser.add_argument("--api", default="http://127.0.0.1:8001", help="FastAPI base URL")
    parser.add_argument("--force", action="store_true", help="Re-seed even if patient already exists.")
    args = parser.parse_args()

    base = args.api.rstrip("/")
    files = sorted(MOCK_DIR.glob("patient_*.json"))
    if not files:
        print(f"No mock JSON files found at {MOCK_DIR}")
        return 1

    with httpx.Client(timeout=120.0) as client:
        try:
            existing = client.get(f"{base}/api/patients").raise_for_status().json()
        except httpx.HTTPError as exc:
            print(f"Could not reach {base}/api/patients: {exc}")
            return 2
        existing_ids = {row.get("zep_user_id") for row in existing if isinstance(row, dict)}
        print(f"Found {len(existing_ids)} existing patients in InsForge.")

        created = 0
        skipped = 0
        failed = 0

        for path in files:
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                print(f"  ! {path.name}: invalid JSON ({exc})")
                failed += 1
                continue

            zep_user_id = record.get("zep_user_id")
            display_name = record.get("display_name") or path.stem.replace("_", " ").title()
            if not zep_user_id:
                print(f"  ! {path.name}: missing zep_user_id, skipping")
                failed += 1
                continue

            if zep_user_id in existing_ids and not args.force:
                print(f"  - {path.name}: {display_name} ({zep_user_id}) already exists, skipping")
                skipped += 1
                continue

            demographics = record.get("demographics") or {}
            payload = {
                "zep_user_id": zep_user_id,
                "display_name": display_name,
                "age": derive_age(demographics.get("age_band")),
                "sex": "O",
                "primary_doctor": derive_primary_doctor(record),
                "notes": record.get("notes"),
                "tags": list(record.get("tags") or []),
            }
            try:
                resp = client.post(f"{base}/api/patients", json=payload)
                resp.raise_for_status()
                row = resp.json()
                print(
                    f"  + {path.name}: created {row.get('name')} "
                    f"id={row.get('id')} zep={row.get('zep_user_id')}"
                )
                created += 1
            except httpx.HTTPStatusError as exc:
                detail = exc.response.text[:200]
                print(f"  ! {path.name}: HTTP {exc.response.status_code}: {detail}")
                failed += 1
            except httpx.HTTPError as exc:
                print(f"  ! {path.name}: {exc}")
                failed += 1

        print(
            f"\nSummary: {created} created, {skipped} skipped, {failed} failed "
            f"(of {len(files)} total)."
        )
        return 0 if failed == 0 else 3


if __name__ == "__main__":
    sys.exit(main())
