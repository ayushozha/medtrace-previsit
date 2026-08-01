"""Shared FastAPI dependencies: env-loaded config + per-request helpers."""

from __future__ import annotations

import os
from functools import lru_cache

from fastapi import Depends, HTTPException, status

from medtrace_agent.insforge_api import insforge_persistence_enabled
from medtrace_agent.local_store import local_mock_enabled, local_profile_id


@lru_cache(maxsize=1)
def get_demo_profile_id() -> str:
    """Read the seeded demo profile id from env (single-profile mode)."""
    if local_mock_enabled():
        return local_profile_id()
    pid = (os.environ.get("INSFORGE_PROFILE_ID") or "").strip()
    if not pid:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "INSFORGE_PROFILE_ID is not set. Seed a profiles row and add the "
                "uuid to the root .env (see DBMS-design.md), or set "
                "MEDTRACE_LOCAL_MOCK=1 for offline fixtures."
            ),
        )
    return pid


def require_insforge_enabled() -> None:
    if not insforge_persistence_enabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "InsForge persistence is not configured. Set INSFORGE_URL, "
                "INSFORGE_API_KEY, INSFORGE_PROFILE_ID in .env — or set "
                "MEDTRACE_LOCAL_MOCK=1 to use local fake patients."
            ),
        )


RequireInsforgeDep = Depends(require_insforge_enabled)
