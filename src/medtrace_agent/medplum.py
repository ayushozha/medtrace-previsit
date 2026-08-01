"""Centralized OAuth2 and FHIR R4 REST client for a Medplum server.

The client is backend-only.  It deliberately exposes small primitives rather
than leaking tokens, HTTP responses, or Medplum-specific errors to API routes.
"""

from __future__ import annotations

import os
import threading
import time
from functools import lru_cache
from typing import Any, Mapping
from urllib.parse import urljoin, urlparse

import httpx


FHIR_JSON = "application/fhir+json"


class MedplumError(RuntimeError):
    """Sanitized error raised for Medplum authentication or FHIR failures."""

    def __init__(self, message: str, *, status_code: int | None = None, outcome: dict[str, Any] | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.outcome = outcome


def medplum_configured() -> bool:
    return bool(
        (os.environ.get("MEDPLUM_BASE_URL") or "").strip()
        and (os.environ.get("MEDPLUM_CLIENT_ID") or "").strip()
        and (os.environ.get("MEDPLUM_CLIENT_SECRET") or "").strip()
    )


def _outcome_message(data: Any) -> str | None:
    if not isinstance(data, dict) or data.get("resourceType") != "OperationOutcome":
        return None
    messages: list[str] = []
    for issue in data.get("issue") or []:
        if not isinstance(issue, dict):
            continue
        detail = issue.get("diagnostics")
        if not detail and isinstance(issue.get("details"), dict):
            detail = issue["details"].get("text")
        if detail:
            messages.append(str(detail))
    return "; ".join(messages[:3]) or "FHIR operation failed"


class MedplumClient:
    """Synchronous FHIR R4 client suitable for FastAPI thread-pool handlers."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self.base_url = (base_url or os.environ.get("MEDPLUM_BASE_URL") or "http://localhost:8103/").rstrip("/") + "/"
        self.fhir_url = urljoin(self.base_url, "fhir/R4/")
        self.client_id = client_id if client_id is not None else (os.environ.get("MEDPLUM_CLIENT_ID") or "")
        self.client_secret = client_secret if client_secret is not None else (os.environ.get("MEDPLUM_CLIENT_SECRET") or "")
        self.timeout = timeout or float(os.environ.get("MEDPLUM_TIMEOUT_SECONDS", "30"))
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._token_lock = threading.Lock()

    def _authenticate(self, *, force: bool = False) -> str:
        with self._token_lock:
            if not force and self._token and time.monotonic() < self._token_expires_at:
                return self._token
            if not self.client_id or not self.client_secret:
                raise MedplumError("Medplum client credentials are not configured.")
            try:
                response = httpx.post(
                    urljoin(self.base_url, "oauth2/token"),
                    data={
                        "grant_type": "client_credentials",
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                    },
                    headers={"Accept": "application/json"},
                    timeout=self.timeout,
                )
            except httpx.HTTPError as exc:
                raise MedplumError("Unable to reach the Medplum OAuth endpoint.") from exc
            data = self._json_or_none(response)
            if response.status_code >= 400 or not isinstance(data, dict) or not data.get("access_token"):
                raise MedplumError(
                    _outcome_message(data) or "Medplum client authentication failed.",
                    status_code=response.status_code,
                    outcome=data if isinstance(data, dict) else None,
                )
            self._token = str(data["access_token"])
            expires_in = max(30, int(data.get("expires_in") or 3600))
            self._token_expires_at = time.monotonic() + max(1, expires_in - 30)
            return self._token

    @staticmethod
    def _json_or_none(response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError:
            return None

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
        content: bytes | None = None,
        headers: Mapping[str, str] | None = None,
        retry_auth: bool = True,
        expect_json: bool = True,
    ) -> Any:
        url = path if path.startswith("http://") or path.startswith("https://") else urljoin(self.fhir_url, path.lstrip("/"))
        expected = urlparse(self.base_url)
        actual = urlparse(url)
        if (actual.scheme, actual.netloc) != (expected.scheme, expected.netloc):
            raise MedplumError("Refusing to follow a FHIR link outside the configured Medplum server.")
        request_headers = {
            "Authorization": f"Bearer {self._authenticate()}",
            "Accept": FHIR_JSON,
        }
        if json is not None:
            request_headers["Content-Type"] = FHIR_JSON
        if headers:
            request_headers.update(headers)
        try:
            response = httpx.request(
                method,
                url,
                params=params,
                json=json,
                content=content,
                headers=request_headers,
                timeout=self.timeout,
            )
        except httpx.HTTPError as exc:
            raise MedplumError("Unable to reach the Medplum FHIR endpoint.") from exc
        if response.status_code == 401 and retry_auth:
            self._authenticate(force=True)
            return self._request(
                method,
                path,
                params=params,
                json=json,
                content=content,
                headers=headers,
                retry_auth=False,
                expect_json=expect_json,
            )
        if response.status_code >= 400:
            data = self._json_or_none(response)
            raise MedplumError(
                _outcome_message(data) or f"Medplum FHIR request failed ({response.status_code}).",
                status_code=response.status_code,
                outcome=data if isinstance(data, dict) else None,
            )
        if expect_json:
            data = self._json_or_none(response)
            if not isinstance(data, dict):
                raise MedplumError("Medplum returned a non-FHIR response.", status_code=response.status_code)
            return data
        return response.content

    def read(self, resource_type: str, resource_id: str) -> dict[str, Any]:
        return self._request("GET", f"{resource_type}/{resource_id}")

    def create(self, resource: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", str(resource["resourceType"]), json=resource)

    def update(self, resource: dict[str, Any]) -> dict[str, Any]:
        meta = resource.get("meta") if isinstance(resource.get("meta"), dict) else {}
        version_id = str(meta.get("versionId") or "")
        headers = {"If-Match": f'W/"{version_id}"'} if version_id else None
        return self._request(
            "PUT",
            f"{resource['resourceType']}/{resource['id']}",
            json=resource,
            headers=headers,
        )

    def conditional_upsert(self, resource: dict[str, Any], *, identifier: str) -> dict[str, Any]:
        return self._request("PUT", str(resource["resourceType"]), params={"identifier": identifier}, json=resource)

    def transaction(self, entries: list[dict[str, Any]]) -> dict[str, Any]:
        return self._request("POST", "", json={"resourceType": "Bundle", "type": "transaction", "entry": entries})

    def batch(self, entries: list[dict[str, Any]]) -> dict[str, Any]:
        return self._request("POST", "", json={"resourceType": "Bundle", "type": "batch", "entry": entries})

    def search_bundle(self, resource_type: str, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return self._request("GET", resource_type, params=params)

    def search(self, resource_type: str, params: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
        resources: list[dict[str, Any]] = []
        bundle = self.search_bundle(resource_type, params)
        while True:
            for entry in bundle.get("entry") or []:
                resource = entry.get("resource") if isinstance(entry, dict) else None
                if isinstance(resource, dict) and resource.get("resourceType") == resource_type:
                    resources.append(resource)
            next_url = next(
                (str(link.get("url")) for link in bundle.get("link") or [] if isinstance(link, dict) and link.get("relation") == "next" and link.get("url")),
                None,
            )
            if not next_url:
                return resources
            bundle = self._request("GET", next_url)

    def search_one(self, resource_type: str, params: Mapping[str, Any]) -> dict[str, Any] | None:
        rows = self.search(resource_type, {**params, "_count": 1})
        return rows[0] if rows else None

    def create_binary(
        self,
        data: bytes,
        *,
        content_type: str,
        security_context: str | None = None,
    ) -> dict[str, Any]:
        headers = {"Content-Type": content_type or "application/octet-stream"}
        if security_context:
            # Medplum uses this FHIR header to set Binary.securityContext at upload
            # time.  Without it, patient files fall back to project-wide Binary
            # permissions rather than inheriting the patient's compartment access.
            headers["X-Security-Context"] = security_context
        return self._request(
            "POST",
            "Binary",
            content=data,
            headers=headers,
        )

    def read_binary(self, binary_id: str) -> bytes:
        return self._request("GET", f"Binary/{binary_id}", headers={"Accept": "*/*"}, expect_json=False)

    def validate(self, resource: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", f"{resource['resourceType']}/$validate", json=resource)

    def reachable(self) -> bool:
        try:
            response = httpx.get(urljoin(self.base_url, "healthcheck"), timeout=min(self.timeout, 3.0))
            return response.status_code < 500
        except httpx.HTTPError:
            return False


@lru_cache(maxsize=1)
def get_medplum_client() -> MedplumClient:
    return MedplumClient()


def clear_medplum_client_cache() -> None:
    get_medplum_client.cache_clear()
