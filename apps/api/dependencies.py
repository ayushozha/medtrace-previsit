"""Shared FastAPI dependencies: env-loaded config + per-request helpers."""

from __future__ import annotations

from fastapi import Depends, HTTPException, status

from medtrace_agent.medplum import medplum_configured


def require_medplum_enabled() -> None:
    if not medplum_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Medplum persistence is not configured. Start the local stack, create a "
                "ClientApplication, and set MEDPLUM_BASE_URL, MEDPLUM_CLIENT_ID, and "
                "MEDPLUM_CLIENT_SECRET in the root .env."
            ),
        )


RequireMedplumDep = Depends(require_medplum_enabled)
