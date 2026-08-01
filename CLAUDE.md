# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Clinical AI tooling in one monorepo: **one FastAPI service** and **one React app**, sharing the
`src/medtrace_agent/` Python package.

- **`apps/api/`** (`apps.api.main:app`, port **8001**) — clinical routes (patients, documents,
  chat threads, derived clinical views) plus imaging routes (DICOM upload, MedSAM2 segmentation,
  draft reports).
- **`apps/web/`** (Vite + React 19 + Tailwind v4, port **3000**) — three routes: `/` and
  `/patients/:id` (dashboard), `/imaging` (DICOM viewer), `/session` (voice consultation).

`services/transcription/` is a preserved prototype backing the `/session` route: a LangGraph
backend (8010) behind a CopilotKit Express runtime (4000). It is **not** part of `npm run dev`.

Depth references: `README.md`, `AGENTS.md` (layout + gotchas), `DBMS-design.md` (InsForge schema).

## Commands

| Script | Starts | Ports |
|--------|--------|-------|
| `npm run dev` | api + web | 8001, 3000 |
| `npm run dev:api` | FastAPI only | 8001 |
| `npm run dev:web` | Vite only | 3000 |
| `npm run dev:transcription` | transcription backend + CopilotKit runtime | 8010, 4000 |

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
cp .env.example .env                        # or set MEDTRACE_LOCAL_MOCK=1 to run offline
```

Optional extras: `.[medgemma-local]` (torch/transformers, only for `MEDGEMMA_MODEL_ID`);
`services/transcription/backend/requirements.txt` for the `/session` prototype.

### Tests

```bash
.venv/bin/pytest -m "not integration"              # 49 tests
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
| `ingest/documents.py`, `ingest/scan_extract.py` | PDF → text via VLM page images or `pypdf`; `chunk_for_zep` → `graph.add`. |
| `ontology/clinical.py` | Clinical entity/edge ontology; `auto_apply_clinical_ontology` runs at API startup. |
| `imaging/` | `storage.py` (study paths), `dicom.py` (preview render), `model_adapters/` (MedSAM2, report). |
| `insforge_api.py` | InsForge Postgres + Storage registry. `@local_mock_fallback` routes each call to `local_store`. |
| `local_store.py` | File-backed stand-in for InsForge (`MEDTRACE_LOCAL_MOCK=1`). |
| `fireworks_config.py` | `fireworks_chat_client(...)` — the **only** `ChatOpenAI` construction point. |
| `env.py` | `load_repo_env()` — `.env` then `.env.local`, both with `override=True`. |
| `patient_json.py`, `tracing.py` | Demo fixtures + derivations; Langtrace/LangSmith init. |

**Zep model (central concept):** a patient is a Zep **user** (`zep_user_id`). Short dialog + rolling
context lives on **threads**; durable episodes/facts/ontology live on the **graph**. New thread =
new conversation, same user.

**Ontology is a startup dependency.** `apps/api` calls `auto_apply_clinical_ontology()` in its
lifespan hook. `routers/clinical.py` searches Zep by those custom node labels and edge types — if
registration is skipped, every clinical endpoint returns an empty array with a 200. Re-apply
manually with `scripts/apply_ontology.py`.

### API (`apps/api/`, port 8001)

Single-demo-profile mode: `INSFORGE_PROFILE_ID` (a `public.profiles.id` uuid) is required unless
local mock is on. Routers: `patients`, `documents`, `threads`, `clinical`, `studies`.
`GET /api/health` reports `insforge_configured`, `local_mock`, `fireworks_configured`,
`zep_configured` and an `imaging` block. Clinical data routes 503 unless
`require_insforge_enabled` passes; imaging routes never do (they degrade to mock).

**No SQL mirror of clinical data.** `routers/clinical.py` derives every dashboard field
per-request from Zep — ontology-scoped `graph.search` plus regex over episode text
(`_LAB_HINT_PATTERNS`). InsForge only stores chart/document/session rows.

**`GET /api/patients/{id}/snapshot` has two paths**: if `chart_subjects.metadata.clinical` exists it
is validated straight into the response (skipping Zep); otherwise it falls back to the derived
builders. Local-mock takes the first path.

`/data` is mounted from repo-root `data/`, serving `data/studies/{id}/preview.png` and
segmentation overlays. `data/studies/` is gitignored.

### Local mock mode (`MEDTRACE_LOCAL_MOCK=1`)

Runs the dashboard with **no InsForge and no Zep reads**:

- `@local_mock_fallback` in `insforge_api.py` routes each persistence call to the same-named
  `local_store` function — the two must stay signature-compatible.
- State lives in `data/local_mock/store.json` (gitignored), auto-seeded from
  `mock/patient_data/patient_*.json` plus clinical fixtures. `scripts/reset_local_mock.py` rebuilds it.
- **Chat and ingest still call Zep and Fireworks** and fail without keys. Creating a thread calls
  `ensure_user` first, so locally-seeded charts work against a real Zep project.

### Imaging model adapters (`src/medtrace_agent/imaging/model_adapters/`)

`MedSAM2Service` and `MedGemmaService` resolve a mode in order: **HTTP endpoint**
(`MEDSAM2_ENDPOINT` / `MEDGEMMA_ENDPOINT`) → **local adapter** (`MEDSAM2_ADAPTER_MODULE` /
`MEDGEMMA_MODEL_ID`) → **deterministic mock**. Reports use Qwen VL via Nebius
(`NEBIUS_API_KEY`, `NEBIUS_BASE_URL`, `NEBIUS_QWEN_VL_MODEL`); mock without the key.

**DICOM handling:** `pydicom`, rescaled via `RescaleSlope`/`RescaleIntercept`, windowed via
`WindowCenter`/`WindowWidth`. ROI prompts are normalised 0–1 and converted to pixels backend-side.

### Web app (`apps/web/`)

One Vite app, one design system (Tailwind v4 tokens in `src/index.css`, primitives in
`src/components/ui/`), one typed client (`src/lib/api.ts` + `src/lib/imagingApi.ts`) and one type
file mirroring `apps/api/schemas.py` (`src/lib/types.ts`).

| Route | Component | Notes |
|-------|-----------|-------|
| `/`, `/patients/:id` | `MainDashboard`, `DashboardHome` | Directory + chart, chat, documents |
| `/imaging` | `components/imaging/` | Dark viewer (deliberate for radiology), ROI drag → segmentation |
| `/session` | `components/session/` | **Lazy-loaded** — CopilotKit + tiptap are ~2 MB |

The `/session` route carries its own `session.css`; the other routes are pure Tailwind.

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
- **The `/session` route needs two extra processes** (`npm run dev:transcription`) and uses
  **OpenAI directly** (`OPENAI_API_KEY`, Whisper + `tts-1`), not Fireworks.
- **A pydicom sample for testing uploads**:
  `.venv/bin/python -c "import pydicom.data,os;print(os.path.join(os.path.dirname(pydicom.data.__file__),'test_files','MR_small.dcm'))"`
- **Vision ingest cost**: one multimodal call per PDF page. Cap with the `dpi` / `max_pages` form
  fields on the upload route, or `PDF_VL_MAX_PAGES` / `PDF_VL_DPI`.
- `git tag pre-consolidation` marks the tree before the Streamlit UI, `services/medsamlite/` and the
  two standalone frontends were removed — recover from history if needed.
