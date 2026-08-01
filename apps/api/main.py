"""FastAPI entry point for the Medtrace web frontend.

Run::

    uvicorn apps.api.main:app --reload --port 8001

Serves the clinical stack (Zep memory + graph, Fireworks, InsForge) and the imaging
stack (DICOM upload, segmentation, draft reports) from one app.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Repo layout: .../<repo>/apps/api/main.py → add .../<repo>/src so the package imports
# when running uvicorn from the repo root without an editable install.
_SRC = Path(__file__).resolve().parents[2] / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from medtrace_agent.env import load_repo_env  # noqa: E402

load_repo_env()

# Optional: Langtrace must be initialised before LangChain / LangGraph imports.
# LangSmith is auto-instrumented by LangChain when LANGSMITH_TRACING + LANGSMITH_API_KEY
# are present in the environment — load_repo_env above puts them there before any
# LangChain modules are imported, so the auto-tracer picks them up.
from medtrace_agent.tracing import init_langtrace, log_tracing_status  # noqa: E402

init_langtrace()
log_tracing_status()

from contextlib import asynccontextmanager  # noqa: E402

from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from apps.api.routers import clinical, documents, patients, studies, threads  # noqa: E402
from medtrace_agent.imaging.storage import data_dir  # noqa: E402
from medtrace_agent.ontology import auto_apply_clinical_ontology  # noqa: E402


def _cors_origins() -> list[str]:
    raw = os.environ.get("API_CORS_ORIGINS") or "http://localhost:3000,http://127.0.0.1:3000"
    return [o.strip() for o in raw.split(",") if o.strip()]


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Register the clinical ontology with Zep before serving traffic.

    ``routers/clinical.py`` searches the graph by the custom node labels and edge types
    this defines, so without registration every clinical endpoint returns empty arrays
    with a 200. Failures are logged, not raised — the imaging routes and health check
    must still come up without Zep.
    """
    auto_apply_clinical_ontology()
    yield


app = FastAPI(
    title="Medtrace API",
    version="0.2.0",
    description=(
        "Clinical routes bridge the web frontend to Zep Cloud (memory + graph), "
        "Fireworks AI (LLM + VLM) and InsForge (Postgres + Storage) in "
        "single-demo-profile mode. Imaging routes handle DICOM upload, MedSAM2 "
        "segmentation and draft reports."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health", tags=["meta"])
def health() -> dict[str, object]:
    """Liveness + sanity checks (does not call out to Zep / Fireworks)."""
    from medtrace_agent.insforge_api import insforge_persistence_enabled
    from medtrace_agent.local_store import local_mock_enabled

    return {
        "status": "ok",
        "insforge_configured": insforge_persistence_enabled(),
        "local_mock": local_mock_enabled(),
        "fireworks_configured": bool(os.environ.get("FIREWORKS_API_KEY")),
        "zep_configured": bool(os.environ.get("ZEP_API_KEY")),
        "demo_profile_id_set": bool(
            os.environ.get("INSFORGE_PROFILE_ID") or local_mock_enabled()
        ),
        # Which provider the imaging report route will use (mock when unconfigured).
        "imaging": studies.medgemma_service.status(),
    }


app.include_router(patients.router)
app.include_router(documents.router)
app.include_router(threads.router)
app.include_router(clinical.router)
app.include_router(studies.router)

# Study previews and segmentation overlays are referenced by URL in API responses.
app.mount("/data", StaticFiles(directory=data_dir()), name="data")
