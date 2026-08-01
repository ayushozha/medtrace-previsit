"""Minimal Medplum OAuth, pre-write validation, and FHIR transaction client."""

from __future__ import annotations

import hmac
import os
import re
from typing import Any
from urllib.parse import urlparse

import httpx

from medtrace_agent.integrations.sponsor_error import SponsorIntegrationError

_DEFAULT_BASE_URL = "https://api.medplum.com"
SYNTHETIC_TAG_SYSTEM = "https://github.com/ayushozha/medtrace-previsit/tags"
SYNTHETIC_TAG_CODE = "synthetic-demo"
CHART_IDENTIFIER_SYSTEM = (
    "https://github.com/ayushozha/medtrace-previsit/insforge-chart-subject-id"
)
_FHIR_ID = re.compile(r"[A-Za-z0-9\-.]{1,64}")


def _official_base_url(value: str) -> bool:
    parsed = urlparse(value)
    try:
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and parsed.hostname == "api.medplum.com"
        and parsed.username is None
        and parsed.password is None
        and port in {None, 443}
        and not parsed.path.rstrip("/")
        and not parsed.query
        and not parsed.fragment
    )


def configuration_status() -> dict[str, object]:
    required = ("MEDPLUM_CLIENT_ID", "MEDPLUM_CLIENT_SECRET", "MEDPLUM_PATIENT_ID")
    missing = [name for name in required if not (os.environ.get(name) or "").strip()]
    base_url = (os.environ.get("MEDPLUM_BASE_URL") or _DEFAULT_BASE_URL).rstrip("/")
    if not _official_base_url(base_url):
        missing.append("MEDPLUM_BASE_URL must use https://api.medplum.com")
    patient_id = (os.environ.get("MEDPLUM_PATIENT_ID") or "").strip()
    if patient_id and not _FHIR_ID.fullmatch(patient_id):
        missing.append("MEDPLUM_PATIENT_ID must be a valid FHIR id")
    return {"configured": not missing, "missing": missing}


def _required(name: str) -> str:
    value = (os.environ.get(name) or "").strip()
    if not value:
        raise SponsorIntegrationError(
            "medplum", f"{name} is required for the real Medplum FHIR path.", status_code=503
        )
    return value


def patient_reference() -> str:
    patient_id = _required("MEDPLUM_PATIENT_ID")
    if not _FHIR_ID.fullmatch(patient_id):
        raise SponsorIntegrationError(
            "medplum", "MEDPLUM_PATIENT_ID must be a valid FHIR id.", status_code=503
        )
    return f"Patient/{patient_id}"


class MedplumClient:
    def __init__(self) -> None:
        self.base_url = (os.environ.get("MEDPLUM_BASE_URL") or _DEFAULT_BASE_URL).rstrip("/")
        if not _official_base_url(self.base_url):
            raise SponsorIntegrationError(
                "medplum",
                "MEDPLUM_BASE_URL must use the hosted https://api.medplum.com sponsor endpoint.",
                status_code=503,
            )
        try:
            self.timeout = float(os.environ.get("MEDPLUM_TIMEOUT_SECONDS") or "45")
        except ValueError as exc:
            raise SponsorIntegrationError(
                "medplum", "MEDPLUM_TIMEOUT_SECONDS must be numeric.", status_code=500
            ) from exc
        if self.timeout <= 0:
            raise SponsorIntegrationError(
                "medplum", "MEDPLUM_TIMEOUT_SECONDS must be positive.", status_code=500
            )

    async def _token(self) -> str:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/oauth2/token",
                    data={
                        "grant_type": "client_credentials",
                        "client_id": _required("MEDPLUM_CLIENT_ID"),
                        "client_secret": _required("MEDPLUM_CLIENT_SECRET"),
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
        except httpx.HTTPError as exc:
            raise SponsorIntegrationError("medplum", f"Medplum authentication failed: {exc}") from exc
        if not response.is_success:
            raise SponsorIntegrationError(
                "medplum", f"Medplum authentication was rejected ({response.status_code})."
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise SponsorIntegrationError("medplum", "Medplum returned unreadable OAuth output.") from exc
        token = str(payload.get("access_token") or "") if isinstance(payload, dict) else ""
        if not token:
            raise SponsorIntegrationError("medplum", "Medplum returned no access token.")
        return token

    async def assert_synthetic_patient(self, chart_subject_id: str) -> dict[str, Any]:
        """Require an explicit two-way synthetic chart binding before any FHIR write."""
        patient_id = patient_reference().split("/", 1)[1]
        token = await self._token()
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(
                    f"{self.base_url}/fhir/R4/Patient/{patient_id}",
                    headers={"Authorization": f"Bearer {token}", "Accept": "application/fhir+json"},
                )
        except httpx.HTTPError as exc:
            raise SponsorIntegrationError(
                "medplum", f"Medplum synthetic patient verification failed: {exc}"
            ) from exc
        if not response.is_success:
            raise SponsorIntegrationError(
                "medplum",
                f"Medplum rejected synthetic patient verification ({response.status_code}).",
            )
        try:
            patient = response.json()
        except ValueError as exc:
            raise SponsorIntegrationError(
                "medplum", "Medplum returned an unreadable Patient resource."
            ) from exc
        if not isinstance(patient, dict) or patient.get("resourceType") != "Patient":
            raise SponsorIntegrationError("medplum", "Medplum returned an invalid Patient resource.")
        meta = patient.get("meta") if isinstance(patient.get("meta"), dict) else {}
        tags = [item for item in meta.get("tag") or [] if isinstance(item, dict)]
        identifiers = [item for item in patient.get("identifier") or [] if isinstance(item, dict)]
        tagged = any(
            item.get("system") == SYNTHETIC_TAG_SYSTEM and item.get("code") == SYNTHETIC_TAG_CODE
            for item in tags
        )
        bound = any(
            item.get("system") == CHART_IDENTIFIER_SYSTEM
            and hmac.compare_digest(str(item.get("value") or ""), chart_subject_id)
            for item in identifiers
        )
        if not tagged or not bound:
            raise SponsorIntegrationError(
                "medplum",
                "The configured Patient lacks the required synthetic tag and InsForge chart binding.",
                status_code=409,
            )
        return patient

    @staticmethod
    def _validation_issues(payload: dict[str, Any]) -> tuple[list[str], list[str]]:
        errors: list[str] = []
        notices: list[str] = []
        for issue in payload.get("issue") or []:
            if not isinstance(issue, dict):
                continue
            details = issue.get("details") if isinstance(issue.get("details"), dict) else {}
            detail = str(issue.get("diagnostics") or details.get("text") or "FHIR issue")
            if issue.get("severity") in {"fatal", "error"}:
                errors.append(detail)
            else:
                notices.append(detail)
        return errors, notices

    @classmethod
    def _response_detail(cls, response: httpx.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            return ""
        if not isinstance(payload, dict):
            return ""
        errors, notices = cls._validation_issues(payload)
        detail = "; ".join((errors or notices)[:3])
        return detail[:500]

    async def validate_resources(self, resources: list[dict[str, Any]]) -> list[dict[str, Any]]:
        token = await self._token()
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/fhir+json"}
        validations: list[dict[str, Any]] = []
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for resource in resources:
                resource_type = str(resource.get("resourceType") or "")
                if not resource_type:
                    raise SponsorIntegrationError("medplum", "FHIR resourceType is missing.", status_code=500)
                try:
                    response = await client.post(
                        f"{self.base_url}/fhir/R4/{resource_type}/$validate",
                        headers=headers,
                        json=resource,
                    )
                except httpx.HTTPError as exc:
                    raise SponsorIntegrationError("medplum", f"Medplum validation failed: {exc}") from exc
                if not response.is_success:
                    detail = self._response_detail(response)
                    raise SponsorIntegrationError(
                        "medplum",
                        f"Medplum rejected {resource_type} validation ({response.status_code})"
                        f"{f': {detail}' if detail else '.'}",
                    )
                try:
                    payload = response.json()
                except ValueError as exc:
                    raise SponsorIntegrationError(
                        "medplum", f"Medplum returned unreadable {resource_type} validation output."
                    ) from exc
                if not isinstance(payload, dict):
                    raise SponsorIntegrationError(
                        "medplum", f"Medplum returned invalid {resource_type} validation output."
                    )
                if payload.get("resourceType") != "OperationOutcome":
                    raise SponsorIntegrationError(
                        "medplum",
                        f"Medplum returned a non-OperationOutcome for {resource_type} validation.",
                    )
                errors, notices = self._validation_issues(payload)
                if errors:
                    raise SponsorIntegrationError(
                        "medplum", f"{resource_type} did not validate: {'; '.join(errors)}", status_code=422
                    )
                validations.append({"resource_type": resource_type, "valid": True, "notices": notices})
        return validations

    async def transact(
        self, entries: list[dict[str, Any]], *, checkin_id: str
    ) -> list[dict[str, str | None]]:
        token = await self._token()
        bundle = {"resourceType": "Bundle", "type": "transaction", "entry": entries}
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/fhir/R4",
                    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/fhir+json"},
                    json=bundle,
                )
        except httpx.HTTPError as exc:
            raise SponsorIntegrationError("medplum", f"Medplum transaction failed: {exc}") from exc
        if not response.is_success:
            detail = self._response_detail(response)
            raise SponsorIntegrationError(
                "medplum",
                f"Medplum rejected the FHIR transaction ({response.status_code})"
                f"{f': {detail}' if detail else '.'}",
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise SponsorIntegrationError(
                "medplum", "Medplum returned an unreadable transaction response."
            ) from exc
        if (
            not isinstance(payload, dict)
            or payload.get("resourceType") != "Bundle"
            or payload.get("type") != "transaction-response"
        ):
            raise SponsorIntegrationError("medplum", "Medplum returned an unexpected transaction response.")
        response_entries = payload.get("entry")
        if not isinstance(response_entries, list) or len(response_entries) != len(entries):
            raise SponsorIntegrationError(
                "medplum",
                "Medplum transaction response cardinality did not match the submitted entries.",
            )
        resources: list[dict[str, str | None]] = []
        for entry in response_entries:
            if not isinstance(entry, dict):
                raise SponsorIntegrationError("medplum", "Medplum returned an invalid transaction entry.")
            item = entry.get("response") if isinstance(entry.get("response"), dict) else {}
            status = str(item.get("status") or "")
            if not status.startswith("2"):
                raise SponsorIntegrationError("medplum", f"A Medplum FHIR write failed ({status}).")
            location = str(item.get("location") or "")
            path = urlparse(location).path.rstrip("/")
            match = re.search(r"([^/]+)/([^/]+)(?:/_history/([^/]+))?$", path)
            if match:
                resource_type, resource_id, version_id = match.groups()
            else:
                resource_type = resource_id = version_id = None
            if not resource_type or not resource_id:
                raise SponsorIntegrationError(
                    "medplum", "A successful Medplum write returned no resource location or ID."
                )
            resources.append(
                {
                    "resource_type": resource_type,
                    "resource_id": resource_id,
                    "version_id": version_id,
                    "location": location or None,
                    "status": status,
                    "checkin_id": checkin_id,
                }
            )
        return resources

    async def create_validated(self, resource: dict[str, Any]) -> dict[str, str | None]:
        await self.validate_resources([resource])
        token = await self._token()
        resource_type = str(resource["resourceType"])
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/fhir/R4/{resource_type}",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/fhir+json"},
                json=resource,
            )
        if not response.is_success:
            raise SponsorIntegrationError(
                "medplum", f"Medplum rejected {resource_type} creation ({response.status_code})."
            )
        payload = response.json()
        return {
            "resource_type": resource_type,
            "resource_id": str(payload.get("id") or "") or None,
            "version_id": str((payload.get("meta") or {}).get("versionId") or "") or None,
            "location": None,
            "status": str(response.status_code),
        }
