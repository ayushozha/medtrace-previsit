"""FastAPI entry point for the Medtrace web frontend.

Run::

    uvicorn apps.api.main:app --reload --port 8001

Serves the clinical stack (Medplum FHIR, Zep projection, Fireworks) and the imaging
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

from apps.api.routers import (  # noqa: E402
    demo,
    medplum_consultations,
    medplum_clinical,
    medplum_documents,
    medplum_patients,
    medplum_threads,
    studies,
)
from medtrace_agent.imaging.storage import studies_dir  # noqa: E402
from medtrace_agent.ontology import auto_apply_clinical_ontology  # noqa: E402


def _cors_origins() -> list[str]:
    raw = os.environ.get("API_CORS_ORIGINS") or "http://localhost:3000,http://127.0.0.1:3000"
    return [o.strip() for o in raw.split(",") if o.strip()]


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Register the clinical ontology with Zep before serving traffic.

    This supports optional Zep-powered AI tools. Canonical FHIR dashboard reads do not
    depend on Zep, so failures are logged rather than blocking API startup.
    """
    auto_apply_clinical_ontology()
    yield


app = FastAPI(
    title="Medtrace API",
    version="0.2.0",
    description=(
        "Clinical routes use Medplum FHIR R4 as the canonical store, with Zep as "
        "an AI-memory projection and Fireworks AI for LLM/VLM calls. Imaging routes handle DICOM upload, MedSAM2 "
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
    """Liveness + configuration sanity checks."""
    from medtrace_agent.medplum import get_medplum_client, medplum_configured

    configured = medplum_configured()

    return {
        "status": "ok",
        "clinical_backend": "medplum",
        "medplum_configured": configured,
        "medplum_reachable": get_medplum_client().reachable(),
        "fireworks_configured": bool(os.environ.get("FIREWORKS_API_KEY")),
        "zep_configured": bool(os.environ.get("ZEP_API_KEY")),
        # Which provider the imaging report route will use (mock when unconfigured).
        "imaging": studies.medgemma_service.status(),
    }


app.include_router(medplum_patients.router)
app.include_router(medplum_documents.router)
app.include_router(medplum_threads.router)
app.include_router(medplum_clinical.router)
app.include_router(medplum_consultations.router)
app.include_router(studies.router)
app.include_router(demo.router)

# Study previews and segmentation overlays are referenced by URL in API responses.
app.mount("/data/studies", StaticFiles(directory=studies_dir()), name="studies-data")
