"""Typed failures raised by external sponsor integrations."""

from __future__ import annotations


class SponsorIntegrationError(RuntimeError):
    """A safe, user-presentable provider failure without credentials or payloads."""

    def __init__(self, provider: str, detail: str, *, status_code: int = 502) -> None:
        super().__init__(detail)
        self.provider = provider
        self.detail = detail
        self.status_code = status_code
