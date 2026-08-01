#!/usr/bin/env python
"""Verify local Medplum health, credentials, FHIR access, and validation."""

from __future__ import annotations

from medtrace_agent.env import load_repo_env
from medtrace_agent.medplum import get_medplum_client, medplum_configured
from medtrace_agent.medplum_repository import ZEP_USER_SYSTEM


def main() -> None:
    load_repo_env()
    if not medplum_configured():
        raise SystemExit(
            "Medplum is not configured. Start `npm run medplum:up`, open "
            "http://localhost:3002, create a project ClientApplication, and set "
            "MEDPLUM_BASE_URL, MEDPLUM_CLIENT_ID, and MEDPLUM_CLIENT_SECRET."
        )
    client = get_medplum_client()
    if not client.reachable():
        raise SystemExit("Medplum healthcheck is not reachable at MEDPLUM_BASE_URL.")
    client.search("Patient", {"_count": 1})
    outcome = client.validate({
        "resourceType": "Patient",
        "active": True,
        "identifier": [{"system": ZEP_USER_SYSTEM, "value": "bootstrap-synthetic-patient"}],
        "name": [{"text": "Synthetic Bootstrap Patient"}],
        "gender": "unknown",
    })
    errors = [
        issue for issue in outcome.get("issue") or []
        if isinstance(issue, dict) and issue.get("severity") in {"fatal", "error"}
    ]
    if errors:
        raise SystemExit(f"Medplum $validate rejected the synthetic Patient: {errors}")
    print("Medplum health, OAuth client credentials, Patient search, and FHIR $validate succeeded.")
    print("Review the ClientApplication ProjectMembership AccessPolicy in the Medplum admin app.")


if __name__ == "__main__":
    main()
