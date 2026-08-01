"""Repo-root discovery and ``.env`` loading, shared by the API, scripts and tests.

``override=True`` on both files: the repo ``.env`` wins over whatever is already in the
shell, and ``.env.local`` wins over ``.env``.
"""

from __future__ import annotations

from pathlib import Path

# .../src/medtrace_agent/env.py -> parents[2] is the repo root.
REPO_ROOT = Path(__file__).resolve().parents[2]


def load_repo_env(repo_root: Path | None = None) -> None:
    """Load ``.env`` then ``.env.local`` from the repo root, if present."""
    from dotenv import load_dotenv

    root = repo_root or REPO_ROOT
    load_dotenv(root / ".env", override=True)
    load_dotenv(root / ".env.local", override=True)
