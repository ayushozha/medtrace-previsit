"""Shared operator gate for clinician-reviewed synthetic demo actions."""

from __future__ import annotations

import hmac
import os
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Security
from fastapi.security import APIKeyHeader


DEMO_TOKEN_HEADER = "X-MedTrace-Demo-Token"
OPERATOR_IDENTIFIER_SYSTEM = "https://github.com/ayushozha/medtrace-previsit/operators"
_demo_token_scheme = APIKeyHeader(
    name=DEMO_TOKEN_HEADER,
    scheme_name="MedTraceDemoToken",
    description="Runtime-only operator token for clinician-reviewed synthetic demo actions.",
    auto_error=False,
)


@dataclass(frozen=True)
class DemoOperator:
    operator_id: str
    display_name: str


def require_demo_operator(
    supplied_token: Annotated[str | None, Security(_demo_token_scheme)],
) -> DemoOperator:
    configured_token = (os.environ.get("YC_DEMO_ACCESS_TOKEN") or "").strip()
    if len(configured_token.encode("utf-8")) < 32:
        raise HTTPException(status_code=503, detail="The demo operator gate is not configured.")
    if not supplied_token:
        raise HTTPException(
            status_code=401,
            detail=f"{DEMO_TOKEN_HEADER} is required.",
            headers={"WWW-Authenticate": "MedTraceDemoToken"},
        )
    if not hmac.compare_digest(supplied_token, configured_token):
        raise HTTPException(status_code=403, detail="The demo operator token is invalid.")
    operator_id = (os.environ.get("YC_DEMO_OPERATOR_ID") or "").strip()
    display_name = (os.environ.get("YC_DEMO_OPERATOR_NAME") or "").strip()
    if not operator_id or not display_name:
        raise HTTPException(status_code=503, detail="The server-side demo operator identity is incomplete.")
    return DemoOperator(operator_id=operator_id, display_name=display_name)


DemoOperatorDep = Annotated[DemoOperator, Depends(require_demo_operator)]
