"""Environment-backed model configuration for transcription agents."""

from __future__ import annotations

import os


def openai_model() -> str:
    """Return the configured chat model; production code never chooses one implicitly."""
    value = (os.environ.get("OPENAI_MODEL") or "").strip()
    if not value:
        raise RuntimeError("OPENAI_MODEL is required for transcription and chart agents.")
    return value
