"""Direct Stedi test-mode 270/271 eligibility adapter."""

from __future__ import annotations

import os
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from medtrace_agent.integrations.sponsor_error import SponsorIntegrationError

_DEFAULT_API_URL = "https://healthcare.us.stedi.com/2024-04-01/change/medicalnetwork/eligibility/v3"
TEST_CASE_ID = "aetna-jane-doe-20040404"
_OFFICIAL_TEST_CASE = {
    "STEDI_TRADING_PARTNER_SERVICE_ID": "60054",
    "STEDI_PROVIDER_ORGANIZATION_NAME": "Provider Name",
    "STEDI_PROVIDER_NPI": "1999999984",
    "STEDI_SUBSCRIBER_MEMBER_ID": "AETNA12345",
    "STEDI_SUBSCRIBER_FIRST_NAME": "Jane",
    "STEDI_SUBSCRIBER_LAST_NAME": "Doe",
    "STEDI_SUBSCRIBER_DATE_OF_BIRTH": "20040404",
    "STEDI_SERVICE_TYPE_CODES": "30",
}


def configuration_status() -> dict[str, object]:
    required = (
        "STEDI_TEST_API_KEY",
        "STEDI_TRADING_PARTNER_SERVICE_ID",
        "STEDI_PROVIDER_ORGANIZATION_NAME",
        "STEDI_PROVIDER_NPI",
        "STEDI_SUBSCRIBER_MEMBER_ID",
        "STEDI_SUBSCRIBER_FIRST_NAME",
        "STEDI_SUBSCRIBER_LAST_NAME",
        "STEDI_SUBSCRIBER_DATE_OF_BIRTH",
        "STEDI_SERVICE_TYPE_CODES",
    )
    missing = [name for name in required if not (os.environ.get(name) or "").strip()]
    missing.extend(
        f"{name} must match the documented Stedi Aetna test case"
        for name, expected in _OFFICIAL_TEST_CASE.items()
        if (os.environ.get(name) or "").strip() and (os.environ.get(name) or "").strip() != expected
    )
    return {"configured": not missing, "missing": missing}


def _required(name: str) -> str:
    value = (os.environ.get(name) or "").strip()
    if not value:
        raise SponsorIntegrationError(
            "stedi", f"{name} is required for the real Stedi test-mode path.", status_code=503
        )
    return value


def _decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _money(value: Decimal | None) -> str | None:
    return f"${value:,.2f}" if value is not None else None


def _normalize_benefit(item: dict[str, Any]) -> dict[str, Any]:
    amount = _decimal(item.get("benefitAmount"))
    percent = _decimal(item.get("benefitPercent"))
    return {
        "code": str(item.get("code") or ""),
        "name": str(item.get("name") or ""),
        "benefit_amount": float(amount) if amount is not None else None,
        "benefit_percent": float(percent) if percent is not None else None,
        "coverage_level_code": item.get("coverageLevelCode"),
        "in_plan_network_indicator_code": item.get("inPlanNetworkIndicatorCode"),
        "time_qualifier_code": item.get("timeQualifierCode"),
        "service_type_codes": item.get("serviceTypeCodes") or [],
        "additional_information": item.get("additionalInformation") or [],
    }


def _require_official_test_case() -> None:
    for name, expected in _OFFICIAL_TEST_CASE.items():
        if _required(name) != expected:
            raise SponsorIntegrationError(
                "stedi",
                f"{name} must match the documented Stedi Aetna synthetic test case.",
                status_code=503,
            )


async def check_eligibility() -> dict[str, Any]:
    _require_official_test_case()
    service_codes = [code.strip() for code in _required("STEDI_SERVICE_TYPE_CODES").split(",") if code.strip()]
    subscriber: dict[str, str] = {
        "memberId": _required("STEDI_SUBSCRIBER_MEMBER_ID"),
        "firstName": _required("STEDI_SUBSCRIBER_FIRST_NAME"),
        "lastName": _required("STEDI_SUBSCRIBER_LAST_NAME"),
        "dateOfBirth": _required("STEDI_SUBSCRIBER_DATE_OF_BIRTH"),
    }
    request_body = {
        "controlNumber": str(uuid.uuid4().int)[:9],
        "tradingPartnerServiceId": _required("STEDI_TRADING_PARTNER_SERVICE_ID"),
        "provider": {
            "organizationName": _required("STEDI_PROVIDER_ORGANIZATION_NAME"),
            "npi": _required("STEDI_PROVIDER_NPI"),
        },
        "subscriber": subscriber,
        "encounter": {"serviceTypeCodes": service_codes},
    }
    try:
        timeout = float(os.environ.get("STEDI_TIMEOUT_SECONDS") or "45")
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                _DEFAULT_API_URL,
                headers={
                    "Authorization": f"Key {_required('STEDI_TEST_API_KEY')}",
                    "Content-Type": "application/json",
                },
                json=request_body,
            )
    except (httpx.HTTPError, ValueError) as exc:
        raise SponsorIntegrationError("stedi", f"Stedi eligibility request failed: {exc}") from exc
    if not response.is_success:
        raise SponsorIntegrationError("stedi", f"Stedi rejected the eligibility check ({response.status_code}).")
    try:
        payload = response.json()
    except ValueError as exc:
        raise SponsorIntegrationError("stedi", "Stedi returned an unreadable eligibility response.") from exc
    if not isinstance(payload, dict):
        raise SponsorIntegrationError("stedi", "Stedi returned an invalid eligibility response.")
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    application_mode = str((meta or {}).get("applicationMode") or "").lower()
    if application_mode != "test":
        raise SponsorIntegrationError(
            "stedi",
            "Stedi did not confirm test mode; no eligibility result was persisted.",
            status_code=409,
        )
    raw_benefits = [item for item in payload.get("benefitsInformation") or [] if isinstance(item, dict)]
    benefits = [_normalize_benefit(item) for item in raw_benefits]
    plan_status = payload.get("planStatus") or []
    status_values = [
        str(item.get("statusCode") or item.get("status") or "").strip().lower() in {"1", "active", "active coverage"}
        for item in plan_status
        if isinstance(item, dict)
    ]
    active = any(status_values) if status_values else None
    transaction_id = str(
        payload.get("id") or payload.get("eligibilitySearchId") or meta.get("traceId") or ""
    )
    if not transaction_id:
        raise SponsorIntegrationError("stedi", "Stedi returned no transaction or trace ID.")
    return {
        "transaction_id": transaction_id,
        "trace_id": str(meta.get("traceId") or ""),
        "application_mode": application_mode,
        "coverage_active": active,
        "plan_status": plan_status,
        "benefits": benefits,
        "patient_responsibility_summary": (
            "Not determinable from Stedi test-mode eligibility alone; review each returned "
            "cost-sharing amount with its service, network, coverage-level, and time qualifiers."
        ),
        "disclaimer": (
            "Stedi test mode returns Stedi-generated synthetic test data and does not contact a payer. "
            "It demonstrates eligibility fields, not real coverage or a guaranteed bill."
        ),
    }
