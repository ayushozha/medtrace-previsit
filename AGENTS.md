# AGENTS.md

Guidance for AI coding agents working in this repository. Assumes no prior
knowledge of the project.

## Project overview

A clinical-AI monorepo built around **one FastAPI service** and **one React app**,
sharing the Python package in `src/medtrace_agent/` (installable as
`medtrace-agent`, version 0.2.0). It is a **demo/educational project**, not a
certified medical device — all agent output is framed as non-diagnostic clinical
decision support ("cognitive aid"), and vision-ingest output is demo-grade.

- **`apps/api/`** (`apps.api.main:app`, port 8001) — one service, two concerns:
  - *Clinical*: patients, documents, chat threads, and derived clinical views
    use **Medplum FHIR R4 as the canonical store**. Zep Cloud is a permanent
    derived AI-memory/knowledge projection and Fireworks AI supplies LLM/VLM
    calls. Clinical routes need backend-only Medplum client credentials; Zep
    outages do not block canonical dashboard reads or lose documents/messages.
  - *Imaging*: patient-linked DICOM upload, MedSAM2 segmentation, and draft reports
    (Fireworks VL). Medplum owns `ImagingStudy`, representative patient-scoped
    DICOM `Binary`/`DocumentReference`, `DiagnosticReport`, and review `Task`
    resources; the complete series stays in the local demo viewer store. Model
    inference runs in mock mode without `FIREWORKS_API_KEY`.
- **`apps/web/`** (Vite 6 + React 19 + Tailwind v4, port 3000) — one app with
  `/` (landing), `/patients` + `/patients/:id` (chart), patient-scoped
  `/patients/:id/imaging` and `/patients/:id/session` (voice, lazy-loaded),
  and `/yc-medplum-hackathon-demo`.
- **`services/transcription/`** — prototype backing `/patients/:id/session` and
  the patient-chart CopilotKit co-pilot: LangGraph (8010) + CopilotKit Express
  (4000). Started with `npm run dev:transcription`; STT/TTS via **Deepgram**
  (`DEEPGRAM_API_KEY`), agents via `OPENAI_*` (can point at Fireworks). Session
  agent `predictive_state_updates`. Chart chat uses auto-router `chart_router`
  (specialists: `dashboard_clinical` UI updates, `clinical_memory` Zep/FHIR Q&A;
  research/critic agents can plug in later). Document upload stays on the chart
  (Memory Sources), not inside CopilotChat.

Depth references: `README.md` (Medplum/Zep flows), `CLAUDE.md` (architecture +
gotchas), `DBMS-design.md` (canonical FHIR model).

### Monorepo layout

```
apps/
  api/               FastAPI service — clinical + imaging (apps.api.main:app, 8001)
  web/               React/Vite UI — dashboard, imaging, session, and YC demo (3000)
services/
  transcription/     Voice/CopilotKit prototype (backend 8010, runtime 4000)
src/medtrace_agent/  Shared package (Medplum, Zep, ingest, agents, imaging, ontology)
tests/               Pytest suite (top-level files + tests/unit/)
infra/medplum/       Local pinned Medplum/PostgreSQL/Redis Compose stack
mock/patient_data/   Synthetic JSON fixtures converted to FHIR by the Medplum seed command
data/                Runtime data (historical imports, notes, studies; mostly gitignored)
scripts/             Medplum bootstrap/seed, Zep projection, note ingest, model probe
```

## Setup, build and run

### First-time setup

```bash
# One shared venv at ./.venv. Use .venv/bin/python for all Python work + pytest.
python -m venv .venv
.venv/bin/pip install -e ".[dev,imaging]"

npm install                    # root helper (concurrently)
npm --prefix apps/web ci

cp .env.example .env           # configure backend-only Medplum credentials
npm run medplum:up             # admin :3002, FHIR/OAuth :8103
```

`requirements.txt` is just `-e .[dev,imaging]` with comments — the editable
install is the canonical path. Optional extras: `.[medgemma-local]`
(torch/transformers, only needed for `MEDGEMMA_MODEL_ID`);
`services/transcription/backend/requirements.txt` for the `/session` prototype.

### Running

Prefer the root `package.json` scripts over re-deriving commands.

| Script | Starts | Ports |
|--------|--------|-------|
| `npm run dev` | api + web + Medplum→Zep worker | 8001, 3000 |
| `npm run dev:api` | FastAPI only | 8001 |
| `npm run dev:web` | Vite only | 3000 |
| `npm run dev:transcription` | transcription backend + CopilotKit runtime | 8010, 4000 |
| `npm run medplum:up` | Medplum server + admin app; internal Postgres/Redis | 8103, 3002 |
| `npm run medplum:export-sqlite` | Portable SQLite dump of patient charts for sharing | writes `data/exports/patients.sqlite` |

Running the backend manually:

```bash
.venv/bin/uvicorn apps.api.main:app --host 127.0.0.1 --port 8001 --reload
```

## Tests, lint, type checks

- Python: `.venv/bin/pytest -m "not integration"` (or `npm run test:py`) —
  currently 61 passed, 2 deselected. The `integration` marker hits the live NCBI
  PubMed API and needs network. `pyproject.toml` sets `pythonpath = ["src"]` and
  `testpaths = ["tests"]` (this keeps collection out of `services/`, where
  `test_transcribe.py` is a manual asyncio script, not a pytest module).
  Single file: `.venv/bin/pytest tests/unit/test_rag_chat.py`.
- **Coverage gap to know about**: tests exercise `src/medtrace_agent/` only.
  `apps/api/` has none — verify API changes by running the service and calling
  it (e.g. `curl http://127.0.0.1:8001/api/health`).
- Web: `npm run lint` (`tsc --noEmit`, strict mode) and `npm run build`
  (`vite build`), both scoped to `apps/web`.
- No Python linter/formatter is configured; no CI pipeline exists in the repo.
- `tests/conftest.py` autouse-fixtures clear the Zep client LRU cache and the
  deep-agent `GRAPH_CACHE` between tests — tests that patch `get_zep_client`
  rely on this.

## Architecture

### Shared package: `src/medtrace_agent/`

| Module | Role |
|--------|------|
| `agents/rag_chat.py` | **Fast path**: `chat_with_memory` — one LLM call with system prompt + Zep context + doc catalog. No tool loop. |
| `agents/deep_clinical.py` | **Deep path**: `create_deep_agent` (LangGraph, `deepagents` package) with Zep + PubMed tools, `MemorySaver` keyed by `thread_id`. |
| `zep/memory.py` | Zep client singleton, thread lifecycle, `fetch_thread_context`, `append_turn`. |
| `zep/graph.py` | Read-only graph inspector → `list[dict]` rows; `rows_to_csv` for LLM tool output. |
| `medplum.py` | OAuth token cache and FHIR read/search/upsert/bundle/Binary/pagination primitives. |
| `medplum_repository.py` | FHIR-domain repositories and existing snake_case DTO mappings. |
| `medplum_extraction.py` | Validated typed facts for unverified FHIR resource creation. |
| `ingest/documents.py`, `ingest/scan_extract.py` | PDF → text (VLM page images or `pypdf`), `chunk_for_zep` → `graph.add(type="text")`. |
| `ontology/clinical.py` | Clinical entity/edge ontology; `auto_apply_clinical_ontology` runs at API startup. |
| `imaging/` | `storage.py` (study paths), `dicom.py` (preview render), `fhir.py` (DICOM → ImagingStudy), `model_adapters/` (MedSAM2 + report). |
| `integrations/pubmed.py` | NCBI E-utilities (`esearch`/`esummary` JSON, not scraping). |
| `synthetic_fixtures.py` | Neutral committed fixtures and historical JSON import boundary. |
| `fireworks_config.py` | `fireworks_chat_client(...)` — the single `ChatOpenAI` construction point. |
| `env.py` | `load_repo_env()` — loads `.env` then `.env.local`, both `override=True`. |
| `patient_json.py`, `tracing.py` | Demo fixtures + `derive_age`/`derive_primary_doctor`; Langtrace/LangSmith init. |

**Clinical ownership (central concept):** Medplum owns patients, structured
facts, source files, imaging metadata/reports, and transcripts. `zep_user_id`
and `zep_thread_id` survive as stable FHIR identifiers. Zep holds only a derived
semantic projection, with retry work represented as durable Medplum `Task`
resources.

**Zep ontology is an AI-feature dependency, not a dashboard dependency.** `apps/api` applies it in its lifespan hook
(`AUTO_APPLY_ZEP_ONTOLOGY=true` by default) for semantic agent tools. Ontology
failures are logged, not raised; canonical FHIR clinical endpoints, imaging, and
health still work without Zep. `scripts/apply_ontology.py` re-applies it manually.

**LLM layer:** every call goes through `fireworks_chat_client()` against an
**OpenAI-compatible** endpoint — Fireworks AI by default (`FIREWORKS_BASE_URL`,
`FIREWORKS_MODEL`, `FIREWORKS_VL_MODEL`). `FIREWORKS_VLM_API` picks the vision
transport: `chat` (`/v1/chat/completions`, default) vs `completions`
(`<image>`-prompt style). `FIREWORKS_REASONING_EFFORT=none` keeps Qwen3-style
CoT out of `reasoning_content` so JSON/text lands in `content`. Any
OpenAI-compatible endpoint works if you repoint the env vars (base URL,
model id, and key) — including a self-hosted vLLM server.

### API (`apps/api/`)

Clinical routes always use the `medplum_*` routers. `GET /api/health` reports
`medplum_configured`, `medplum_reachable`, and `zep_configured`. The browser
never receives the client secret. Imaging uploads require an existing canonical
Patient and write Medplum before returning success; model inference remains optional.

Dashboard reads batch `Condition`, `MedicationStatement`, `AllergyIntolerance`,
`Observation`, `Encounter`, `DocumentReference`, `Provenance`, and `Task` and
map them deterministically. Uploads persist `Binary` + `DocumentReference`
before extraction; generated facts are tagged unverified. Canonical chat uses
parent/child `Communication` resources and conditional request identifiers.

### Imaging model adapters (`src/medtrace_agent/imaging/model_adapters/`)

`MedSAM2Service` and `MedGemmaService` resolve a mode in order: **HTTP endpoint**
(`MEDSAM2_ENDPOINT` / `MEDGEMMA_ENDPOINT`) → **local adapter**
(`MEDSAM2_ADAPTER_MODULE` / `MEDGEMMA_MODEL_ID`) → **deterministic mock**.
Draft reports prefer Fireworks VL (`FIREWORKS_API_KEY` + `FIREWORKS_VL_MODEL`)
before the MedGemma HTTP/local paths; mock without a Fireworks key. DICOM previews:
pydicom with `RescaleSlope`/`RescaleIntercept` and windowing
(`WindowCenter`/`WindowWidth`); ROI prompts are normalized 0–1 and converted to
pixels server-side.

### Web app (`apps/web/`)

One design system (Tailwind v4 tokens in `src/index.css`, shadcn-style
primitives in `src/components/ui/`), one typed client (`src/lib/api.ts` +
`src/lib/imagingApi.ts`, same-origin by default — set `VITE_API_BASE_URL` only
for a cross-origin API), and `src/lib/types.ts` mirroring `apps/api/schemas.py`.
Route components live in `src/components/imaging/`, `src/components/session/`,
and `src/components/demo/`; dashboard components at `src/components/` top level.
The session route is lazy-loaded because CopilotKit + tiptap add ~2 MB.
`session.css` is shared by the session workspace and the demo check-in dialog;
the remaining routes are pure Tailwind.

**Vite proxies everything the browser needs**: `/api` and `/data` to the API
(`VITE_API_PROXY_TARGET`, default `http://127.0.0.1:8001`) and
`/api/copilotkit` to the CopilotKit runtime (default `http://localhost:4000`).
The `/api/copilotkit` entry must stay first — Vite matches proxy entries in
insertion order. `src/lib/api.ts` retries GETs on connection failure, because
Vite is serving in ~200 ms while the API needs a second or two to listen.

## Conventions

- **Python**: stdlib plus the deps in `pyproject.toml`. `requires-python >= 3.11`.
  Code uses `from __future__ import annotations`, type hints, and module
  docstrings.
- **API field naming is snake_case** throughout (`apps/api/schemas.py`), and
  `apps/web/src/lib/types.ts` mirrors it verbatim. Do not reintroduce camelCase
  DTOs.
- **Env config**: all runtime config via `.env` at the repo root, loaded through
  `medtrace_agent.env.load_repo_env()`. `.env.example` documents every variable.
  Put comments on their own lines — dotenv treats trailing `# ...` as the value.
- **TypeScript/React**: strict `tsc`; follow the existing component structure
  (hooks in `src/hooks/`, API calls through `src/lib/api.ts`, path alias `@` →
  `src`).
- **Ports are fixed** (8001 api, 3000 web, 8010/4000 transcription). Scripts,
  CORS defaults and frontend defaults all assume them.
- When changing behavior described in `README.md`, `CLAUDE.md`, or this file,
  update the docs in the same change.

## Non-obvious gotchas

- **Vite binds `localhost` (IPv6)** — health-check with
  `curl http://localhost:3000`, not `127.0.0.1`.
- **`.env` inline comments break values** — dotenv treats `KEY=value  # note`
  as part of the value.
- **A sample DICOM for testing uploads** ships with pydicom:
  `.venv/bin/python -c "import pydicom.data,os;print(os.path.join(os.path.dirname(pydicom.data.__file__),'test_files','MR_small.dcm'))"`
- **Vision ingest cost**: one multimodal LLM call per PDF page. Cap with the
  `dpi` / `max_pages` form fields on the upload route, or `PDF_VL_MAX_PAGES`
  (default 25) / `PDF_VL_DPI` (default 150). The `pypdf` "Skip VLM" path reads
  only the embedded text layer — no scans or handwriting.
- **Fireworks model access**: Fireworks retires serverless model ids often, and
  a stale id fails as `404 Model not found`, not an auth error.
  `scripts/fireworks_probe_models.py` lists what your key can actually reach.
- **PubMed**: set `NCBI_EMAIL` (and optionally `NCBI_API_KEY`) for reliable
  E-utilities access; used by the Deep Agent path.
- `git tag pre-consolidation` marks the tree before the Streamlit UI,
  `services/medsamlite/` and the two standalone frontends were removed —
  recover from history if needed.

## Security considerations

- **Never commit secrets.** `.env` is gitignored; `.env.example` holds
  placeholders only. `MEDPLUM_CLIENT_SECRET` is server-side only — never expose it in
  frontend bundles. The same applies to
  `FIREWORKS_API_KEY`, `ZEP_API_KEY`, `OPENAI_API_KEY`, `DEEPGRAM_API_KEY`.
- **Patient-data hygiene**: `data/` (historical imports, uploaded studies, notes)
  is largely gitignored on purpose — `data/studies/`, historical import folders,
  `data/**/*.pdf|.txt|.png|.jpg`. Do not commit real or realistic patient data;
  `data/studies/` in particular holds uploaded DICOM/images. The committed
  fixtures in `mock/patient_data/` are synthetic (no PHI).
- **CORS** is configured via `API_CORS_ORIGINS` (defaults to localhost:3000
  variants) — don't widen it to `*` for the main API.
- **Synthetic-only deployment boundary**: the generic clinical routes have no
  caller authentication; the Medplum dependency checks server configuration,
  not end-user identity. Never connect this demo to a PHI-bearing Medplum
  project. Use a least-privilege ClientApplication scoped to an isolated
  synthetic-only project. The YC operator token protects its workflow routes
  but is not app-wide authentication or RBAC.
- **Demo-grade output**: vision ingest can misread numbers or hallucinate
  structured fields; agent output is non-diagnostic clinical decision support,
  not a medical device. Preserve those disclaimers in code and UI.
