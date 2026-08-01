"""Demo safety checks and async helpers over the canonical Medplum client."""

from __future__ import annotations

import asyncio
import hmac
import os
import re
from typing import Any, Mapping
from urllib.parse import urlparse

from medtrace_agent.integrations.sponsor_error import SponsorIntegrationError
from medtrace_agent.integrations.stedi import TEST_CASE_ID
from medtrace_agent.medplum import (
    MedplumClient as FhirClient,
    MedplumError,
    get_medplum_client,
    medplum_configured,
)
from medtrace_agent.medplum_repository import TAG_SYSTEM, ZEP_USER_SYSTEM, identifier_value


SYNTHETIC_TAG_SYSTEM = TAG_SYSTEM
SYNTHETIC_TAG_CODE = "synthetic"
DEMO_TAG_CODE = "yc-medplum-demo"
DEMO_STEDI_TAG_CODE = TEST_CASE_ID
DEMO_ZEP_USER_ID = "yc-medplum-demo-jane-doe"
_FHIR_ID = re.compile(r"[A-Za-z0-9\-.]{1,64}")


def configuration_status() -> dict[str, object]:
    required = (
        "MEDPLUM_BASE_URL",
        "MEDPLUM_CLIENT_ID",
        "MEDPLUM_CLIENT_SECRET",
        "YC_DEMO_PATIENT_ID",
    )
    missing = [name for name in required if not (os.environ.get(name) or "").strip()]
    patient_id = (os.environ.get("YC_DEMO_PATIENT_ID") or "").strip()
    if patient_id and not _FHIR_ID.fullmatch(patient_id):
        missing.append("YC_DEMO_PATIENT_ID must be a valid FHIR id")
    if not medplum_configured():
        missing.extend(name for name in required[:3] if name not in missing)
    return {"configured": not missing, "missing": list(dict.fromkeys(missing))}


def patient_reference(patient_id: str | None = None) -> str:
    configured = (os.environ.get("YC_DEMO_PATIENT_ID") or "").strip()
    if not configured:
        raise SponsorIntegrationError(
            "medplum", "YC_DEMO_PATIENT_ID is required for the synthetic demo.", status_code=503
        )
    if not _FHIR_ID.fullmatch(configured):
        raise SponsorIntegrationError(
            "medplum", "YC_DEMO_PATIENT_ID must be a valid FHIR id.", status_code=503
        )
    if patient_id and not hmac.compare_digest(patient_id, configured):
        raise SponsorIntegrationError(
            "medplum", "The requested patient is not the configured synthetic demo patient.", status_code=404
        )
    return f"Patient/{configured}"


def _display_name(patient: dict[str, Any]) -> str:
    for name in patient.get("name") or []:
        if not isinstance(name, dict):
            continue
        if name.get("text"):
            return str(name["text"]).strip()
        value = " ".join(
            [*(str(item) for item in name.get("given") or []), str(name.get("family") or "")]
        ).strip()
        if value:
            return value
    return ""


def _sponsor_error(exc: MedplumError, action: str) -> SponsorIntegrationError:
    if exc.status_code in {401, 403}:
        status_code = 503
    elif exc.status_code in {404, 409, 422}:
        status_code = exc.status_code
    else:
        status_code = 502
    return SponsorIntegrationError("medplum", f"{action}: {exc}", status_code=status_code)


def validate_synthetic_patient(
    patient: dict[str, Any], patient_id: str
) -> dict[str, Any]:
    configured_id = patient_reference(patient_id).split("/", 1)[1]
    if patient.get("resourceType") != "Patient" or patient.get("id") != configured_id:
        raise SponsorIntegrationError(
            "medplum", "Medplum returned an invalid Patient resource.", status_code=502
        )
    meta = patient.get("meta") if isinstance(patient.get("meta"), dict) else {}
    tags = {
        str(item.get("code") or "")
        for item in meta.get("tag") or []
        if isinstance(item, dict) and item.get("system") == SYNTHETIC_TAG_SYSTEM
    }
    required_tags = {SYNTHETIC_TAG_CODE, DEMO_TAG_CODE, DEMO_STEDI_TAG_CODE}
    if (
        not required_tags.issubset(tags)
        or _display_name(patient) != "Jane Doe"
        or str(patient.get("birthDate") or "") != "2004-04-04"
        or identifier_value(patient, ZEP_USER_SYSTEM) != DEMO_ZEP_USER_ID
    ):
        raise SponsorIntegrationError(
            "medplum",
            "The configured Patient is not the approved synthetic Jane Doe demo persona.",
            status_code=409,
        )
    return patient


class MedplumClient:
    """Non-blocking demo adapter that reuses the repository's OAuth/FHIR client."""

    def __init__(self, client: FhirClient | None = None) -> None:
        self.client = client or get_medplum_client()

    async def assert_synthetic_patient(self, patient_id: str) -> dict[str, Any]:
        configured_id = patient_reference(patient_id).split("/", 1)[1]
        try:
            patient = await asyncio.to_thread(self.client.read, "Patient", configured_id)
        except MedplumError as exc:
            raise _sponsor_error(exc, "Synthetic patient verification failed") from exc
        return validate_synthetic_patient(patient, configured_id)

    @staticmethod
    def _validation_issues(payload: dict[str, Any]) -> tuple[list[str], list[str]]:
        errors: list[str] = []
        notices: list[str] = []
        for issue in payload.get("issue") or []:
            if not isinstance(issue, dict):
                continue
            details = issue.get("details") if isinstance(issue.get("details"), dict) else {}
            detail = str(issue.get("diagnostics") or details.get("text") or "FHIR issue")
            (errors if issue.get("severity") in {"fatal", "error"} else notices).append(detail)
        return errors, notices

    async def validate_resources(self, resources: list[dict[str, Any]]) -> list[dict[str, Any]]:
        validations: list[dict[str, Any]] = []
        for resource in resources:
            resource_type = str(resource.get("resourceType") or "")
            if not resource_type:
                raise SponsorIntegrationError(
                    "medplum", "FHIR resourceType is missing.", status_code=500
                )
            try:
                payload = await asyncio.to_thread(self.client.validate, resource)
            except MedplumError as exc:
                raise _sponsor_error(exc, f"{resource_type} validation failed") from exc
            if payload.get("resourceType") != "OperationOutcome":
                raise SponsorIntegrationError(
                    "medplum",
                    f"Medplum returned a non-OperationOutcome for {resource_type} validation.",
                )
            errors, notices = self._validation_issues(payload)
            if errors:
                raise SponsorIntegrationError(
                    "medplum",
                    f"{resource_type} did not validate: {'; '.join(errors)}",
                    status_code=422,
                )
            validations.append(
                {"resource_type": resource_type, "valid": True, "notices": notices}
            )
        return validations

    @staticmethod
    def resource_result(
        resource: Mapping[str, Any], *, checkin_id: str, status: str = "200"
    ) -> dict[str, str | None]:
        resource_type = str(resource.get("resourceType") or "") or None
        resource_id = str(resource.get("id") or "") or None
        version_id = str((resource.get("meta") or {}).get("versionId") or "") or None
        location = (
            f"{resource_type}/{resource_id}"
            f"{f'/_history/{version_id}' if version_id else ''}"
            if resource_type and resource_id
            else None
        )
        return {
            "resource_type": resource_type,
            "resource_id": resource_id,
            "version_id": version_id,
            "location": location,
            "status": status,
            "checkin_id": checkin_id,
        }

    async def transact(
        self, entries: list[dict[str, Any]], *, checkin_id: str
    ) -> list[dict[str, str | None]]:
        try:
            payload = await asyncio.to_thread(self.client.transaction, entries)
        except MedplumError as exc:
            raise _sponsor_error(exc, "FHIR transaction failed") from exc
        if payload.get("resourceType") != "Bundle" or payload.get("type") != "transaction-response":
            raise SponsorIntegrationError(
                "medplum", "Medplum returned an unexpected transaction response."
            )
        response_entries = payload.get("entry")
        if not isinstance(response_entries, list) or len(response_entries) != len(entries):
            raise SponsorIntegrationError(
                "medplum",
                "Medplum transaction response cardinality did not match the submitted entries.",
            )
        resources: list[dict[str, str | None]] = []
        for entry in response_entries:
            item = entry.get("response") if isinstance(entry, dict) and isinstance(entry.get("response"), dict) else {}
            status = str(item.get("status") or "")
            if not status.startswith("2"):
                raise SponsorIntegrationError("medplum", f"A Medplum FHIR write failed ({status}).")
            location = str(item.get("location") or "")
            match = re.search(
                r"([^/]+)/([^/]+)(?:/_history/([^/]+))?$", urlparse(location).path.rstrip("/")
            )
            if not match:
                raise SponsorIntegrationError(
                    "medplum", "A successful Medplum write returned no resource location or ID."
                )
            resource_type, resource_id, version_id = match.groups()
            resources.append(
                {
                    "resource_type": resource_type,
                    "resource_id": resource_id,
                    "version_id": version_id,
                    "location": location,
                    "status": status,
                    "checkin_id": checkin_id,
                }
            )
        return resources

    async def create_validated(self, resource: dict[str, Any]) -> dict[str, str | None]:
        await self.validate_resources([resource])
        try:
            created = await asyncio.to_thread(self.client.create, resource)
        except MedplumError as exc:
            raise _sponsor_error(exc, f"{resource.get('resourceType')} creation failed") from exc
        return self.resource_result(created, checkin_id="", status="201")
