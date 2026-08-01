#!/usr/bin/env python
"""Ensure and print local Medplum UI fake accounts for development.

Medplum ships a default super-admin on first boot. This script also ensures a
project-scoped clinician (`dev@medtrace.local`) exists for day-to-day use at
http://localhost:3002.

Requires MEDPLUM_* in `.env` (same ClientApplication used by the API) and a
reachable Medplum server (`npm run medplum:up`).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from urllib.parse import urlencode

from medtrace_agent.env import load_repo_env

DEFAULT_SUPER_ADMIN = ("admin@example.com", "medplum_admin")
DEV_CLINICIAN = ("dev@medtrace.local", "medtrace-dev", "Dev", "Clinician")
PROJECT_ADMIN = ("medtrace-admin@localhost.dev", "MedtraceAdmin123!")


def _env(name: str) -> str:
    value = (os.environ.get(name) or "").strip()
    if not value:
        raise SystemExit(f"Missing {name}. Start Medplum and fill MEDPLUM_* in .env.")
    return value


def _base_url() -> str:
    return _env("MEDPLUM_BASE_URL").rstrip("/")


def _post_json(url: str, payload: dict, headers: dict | None = None) -> tuple[int, dict | str]:
    body = json.dumps(payload).encode()
    req_headers = {"Content-Type": "application/json"}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, data=body, method="POST", headers=req_headers)
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, raw


def _login(email: str, password: str, **extra: str) -> tuple[int, dict | str]:
    payload = {"email": email, "password": password, "scope": "openid offline", **extra}
    return _post_json(f"{_base_url()}/auth/login", payload)


def _pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode()
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    return verifier, challenge


def _access_token_for_project_admin() -> str:
    """Exchange a human project-admin login for a bearer token (PKCE)."""
    client_id = _env("MEDPLUM_CLIENT_ID")
    client_secret = _env("MEDPLUM_CLIENT_SECRET")
    verifier, challenge = _pkce_pair()

    for email, password in (PROJECT_ADMIN, DEFAULT_SUPER_ADMIN):
        status, login = _login(
            email,
            password,
            clientId=client_id,
            codeChallengeMethod="S256",
            codeChallenge=challenge,
        )
        if status == 429:
            raise SystemExit(
                "Medplum login rate-limited. Wait ~60s and re-run "
                "`npm run medplum:dev-account`."
            )
        if status != 200 or not isinstance(login, dict) or not login.get("code"):
            continue
        data = urlencode(
            {
                "grant_type": "authorization_code",
                "code": login["code"],
                "client_id": client_id,
                "client_secret": client_secret,
                "code_verifier": verifier,
            }
        ).encode()
        req = urllib.request.Request(
            f"{_base_url()}/oauth2/token",
            data=data,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(req) as resp:
            token = json.loads(resp.read().decode()).get("access_token")
        if token:
            return token
    raise SystemExit(
        "Could not obtain a project-admin access token. Sign in once at "
        "http://localhost:3002 as admin@example.com / medplum_admin, create a "
        "ClientApplication, and ensure MEDPLUM_* match."
    )


def _ensure_dev_clinician() -> str:
    email, password, first, last = DEV_CLINICIAN
    status, _ = _login(email, password)
    if status == 200:
        return "exists"
    if status == 429:
        raise SystemExit(
            "Medplum login rate-limited. Wait ~60s and re-run "
            "`npm run medplum:dev-account`."
        )

    token = _access_token_for_project_admin()
    project_id = _env("MEDPLUM_PROJECT_ID")
    status, out = _post_json(
        f"{_base_url()}/admin/projects/{project_id}/invite",
        {
            "resourceType": "Practitioner",
            "firstName": first,
            "lastName": last,
            "email": email,
            "password": password,
            "sendEmail": False,
            "membership": {"admin": True},
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    if status in {200, 201}:
        return "created"
    # Already invited (race / prior partial run)
    status2, _ = _login(email, password)
    if status2 == 200:
        return "exists"
    raise SystemExit(f"Invite failed ({status}): {out}")


def main() -> None:
    load_repo_env()
    base = _base_url()
    try:
        with urllib.request.urlopen(f"{base}/healthcheck") as resp:
            if resp.status >= 400:
                raise SystemExit(f"Medplum unhealthy at {base}")
    except urllib.error.URLError as exc:
        raise SystemExit(
            f"Medplum not reachable at {base}. Run `npm run medplum:up` first."
        ) from exc

    state = _ensure_dev_clinician()
    super_email, super_password = DEFAULT_SUPER_ADMIN
    dev_email, dev_password, _, _ = DEV_CLINICIAN

    print("Medplum admin UI: http://localhost:3002")
    print()
    print("Default Medplum super-admin (first boot):")
    print(f"  email:    {super_email}")
    print(f"  password: {super_password}")
    print()
    print(f"Dev clinician ({state}):")
    print(f"  email:    {dev_email}")
    print(f"  password: {dev_password}")
    print()
    print("These are local-only demo accounts — do not use in production.")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
