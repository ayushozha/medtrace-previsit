# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Clinical AI tooling in one monorepo: **one FastAPI service** and **one React app**, sharing the
`src/medtrace_agent/` Python package.

- **`apps/api/`** (`apps.api.main:app`, port **8001**) — clinical routes (patients, documents,
  chat threads, derived clinical views) plus imaging routes (DICOM upload, MedSAM2 segmentation,
  draft reports).
- **`apps/web/`** (Vite + React 19 + Tailwind v4, port **3000**) — `/` (landing),
  `/patients` + `/patients/:id` (dashboard), `/imaging`, `/session`, and
  `/yc-medplum-hackathon-demo`.

`services/transcription/` is a preserved prototype backing the `/session` route: a LangGraph
backend (8010) behind a CopilotKit Express runtime (4000). It is **not** part of `npm run dev`.

Depth references: `README.md`, `AGENTS.md` (layout + gotchas), `DBMS-design.md` (canonical FHIR model).

## Commands

| Script | Starts | Ports |
|--------|--------|-------|
| `npm run dev` | api + web + Zep projection worker | 8001, 3000 |
| `npm run dev:api` | FastAPI only | 8001 |
| `npm run dev:web` | Vite only | 3000 |
| `npm run dev:transcription` | transcription backend + CopilotKit runtime | 8010, 4000 |
| `npm run medplum:up` | Medplum server/admin + internal PostgreSQL/Redis | 8103, 3002 |

```bash
npm run lint      # tsc --noEmit in apps/web
npm run build     # vite build
npm run test:py   # pytest -m "not integration"
```

### First-time setup

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev,imaging]"   # imaging extra = pydicom/numpy/pillow
npm install && npm --prefix apps/web ci
cp .env.example .env                        # configure backend-only Medplum credentials
```

Optional extras: `.[medgemma-local]` (torch/transformers, only for `MEDGEMMA_MODEL_ID`);
`services/transcription/backend/requirements.txt` for the `/session` prototype.

### Tests

```bash
.venv/bin/pytest -m "not integration"              # 61 tests
.venv/bin/pytest tests/unit/test_rag_chat.py       # single file
```

- `integration` hits the **live NCBI PubMed API** — excluded by default. `testpaths = ["tests"]`
  keeps collection out of `services/`.
- Tests cover `src/medtrace_agent/` only. **`apps/api/` has no tests** — verify API changes with
  the curl checks below.
- No Python linter/formatter is configured; pytest is the only dev dependency.

## Architecture

### Shared package: `src/medtrace_agent/`

| Module | Role |
|--------|------|
| `agents/rag_chat.py` | **Fast path**: `chat_with_memory` — one LLM call with system prompt + Zep context + doc catalog. |
| `agents/deep_clinical.py` | **Deep path**: `create_deep_agent` (LangGraph) with Zep + PubMed tools, `MemorySaver` by `thread_id`. Non-diagnostic CDS framing. |
| `zep/memory.py` | Zep client singleton, thread lifecycle, `fetch_thread_context`, `append_turn`. |
| `zep/graph.py` | Read-only graph inspector → `list[dict]` rows (+ `rows_to_csv` for tool output). |
| `medplum.py` | Backend-only OAuth client, FHIR CRUD/search/batch/transaction/Binary/pagination helpers. |
| `medplum_repository.py` | Canonical FHIR repositories and existing DTO mappings. |
| `ingest/documents.py`, `ingest/scan_extract.py` | PDF → text via VLM page images or `pypdf`; `chunk_for_zep` → `graph.add`. |
| `ontology/clinical.py` | Clinical entity/edge ontology; `auto_apply_clinical_ontology` runs at API startup. |
| `imaging/` | `storage.py` (study paths), `dicom.py` (preview render), `model_adapters/` (MedSAM2, report). |
| `synthetic_fixtures.py` | Neutral committed fixtures and historical JSON import boundary. |
| `fireworks_config.py` | `fireworks_chat_client(...)` — the **only** `ChatOpenAI` construction point. |
| `env.py` | `load_repo_env()` — `.env` then `.env.local`, both with `override=True`. |
| `patient_json.py`, `tracing.py` | Demo fixtures + derivations; Langtrace/LangSmith init. |

**Ownership boundary:** Medplum is canonical for patients, structured facts, source documents, and
transcripts. Zep is a permanent but derived AI-memory/knowledge projection. A Zep outage must not
break canonical reads or lose writes; `Task` resources drive retries.

**Zep ontology is an AI-feature dependency, not a dashboard dependency.** `apps/api` still calls
`auto_apply_clinical_ontology()` for semantic tools, but Medplum-backed clinical endpoints read
FHIR directly and remain available if ontology registration or Zep is unavailable.

### API (`apps/api/`, port 8001)

The clinical API always uses the `medplum_*` routers. Server-side
`MEDPLUM_CLIENT_ID` and `MEDPLUM_CLIENT_SECRET` are required; never expose them through Vite.
`GET /api/health` reports `medplum_configured`, `medplum_reachable`, and `zep_configured`.

The YC flow uses `YC_DEMO_PATIENT_ID` as the canonical FHIR Patient ID. A tagged
`DocumentReference` is its approval/readiness journal, and a durable
`zep-demo-projection` Task projects the approved note without blocking canonical
Medplum reads. The operator token is workflow-specific, not app-wide auth; this
repository must use an isolated synthetic-only Medplum project.

Dashboard fields are now mapped deterministically from `Condition`, `MedicationStatement`,
`AllergyIntolerance`, `Observation`, `Encounter`, `DocumentReference`, and `Provenance` in one
FHIR batch. AI extracted facts remain tagged/unverified. `Binary` + `DocumentReference` own source
files; header/child `Communication` resources own chat transcripts.

`GET /api/patients/{id}/snapshot` always uses the FHIR batch mapper.

`/data` is mounted from repo-root `data/`, serving `data/studies/{id}/preview.png` and
segmentation overlays. `data/studies/` is gitignored.

### Imaging model adapters (`src/medtrace_agent/imaging/model_adapters/`)

`MedSAM2Service` and `MedGemmaService` resolve a mode in order: **HTTP endpoint**
(`MEDSAM2_ENDPOINT` / `MEDGEMMA_ENDPOINT`) → **local adapter** (`MEDSAM2_ADAPTER_MODULE` /
`MEDGEMMA_MODEL_ID`) → **deterministic mock**. Reports use Fireworks VL
(`FIREWORKS_API_KEY` + `FIREWORKS_VL_MODEL`); mock without the key.

**DICOM handling:** `pydicom`, rescaled via `RescaleSlope`/`RescaleIntercept`, windowed via
`WindowCenter`/`WindowWidth`. ROI prompts are normalised 0–1 and converted to pixels backend-side.

### Web app (`apps/web/`)

One Vite app, one design system (Tailwind v4 tokens in `src/index.css`, primitives in
`src/components/ui/`), one typed client (`src/lib/api.ts` + `src/lib/imagingApi.ts`) and one type
file mirroring `apps/api/schemas.py` (`src/lib/types.ts`).

| Route | Component | Notes |
|-------|-----------|-------|
| `/` | `LandingPage` | Product landing page and workspace entry point |
| `/patients`, `/patients/:id` | `MainDashboard`, `DashboardHome` | Directory + chart, chat, documents |
| `/imaging` | `components/imaging/` | Dark viewer (deliberate for radiology), ROI drag → segmentation |
| `/session` | `components/session/` | **Lazy-loaded** — CopilotKit + tiptap are ~2 MB |
| `/yc-medplum-hackathon-demo` | `components/demo/` | Synthetic chart, evidence review, Medplum write, Stedi eligibility |

The session workspace and demo check-in dialog share `session.css`; the other routes are pure Tailwind.

**The browser only ever talks to port 3000.** Vite proxies `/api` and `/data` to the API
(`VITE_API_PROXY_TARGET`, default `http://127.0.0.1:8001`) and `/api/copilotkit` to the CopilotKit
runtime. `/api/copilotkit` must stay the first proxy entry — Vite matches them in insertion order.
`VITE_API_BASE_URL` is empty by default and only needed for a cross-origin API.

## Non-obvious gotchas

- **Vite binds `localhost` (IPv6)** — health-check with `curl http://localhost:3000`, not `127.0.0.1`.
- **The API binds `127.0.0.1` (IPv4 only)** while macOS resolves `localhost` to `::1` first, so the
  browser must reach it through the Vite proxy, not directly. `src/lib/api.ts` also retries GETs on
  connection failure: Vite serves in ~200 ms, the API needs a second or two to start listening.
- **`.env` inline comments break values**: dotenv treats `KEY=value  # note` as part of the value.
  Put comments on their own lines.
- **The `/session` route and patient-chart Agent collab need two extra processes**
  (`npm run dev:transcription`). STT/TTS use **Deepgram** (`DEEPGRAM_API_KEY`); session report
  + dashboard checklist agents use an OpenAI-compatible chat endpoint (`OPENAI_*`). Classic
  REST `AIChatPanel` on the chart still works without that stack.
- **A pydicom sample for testing uploads**:
  `.venv/bin/python -c "import pydicom.data,os;print(os.path.join(os.path.dirname(pydicom.data.__file__),'test_files','MR_small.dcm'))"`
- **Vision ingest cost**: one multimodal call per PDF page. Cap with the `dpi` / `max_pages` form
  fields on the upload route, or `PDF_VL_MAX_PAGES` / `PDF_VL_DPI`.
- `git tag pre-consolidation` marks the tree before the Streamlit UI, `services/medsamlite/` and the
  two standalone frontends were removed — recover from history if needed.
