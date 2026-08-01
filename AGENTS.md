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
    backed by Zep Cloud (long-term memory + temporal knowledge graph), Fireworks
    AI (LLM + VLM) and InsForge (Postgres + Storage). Needs `ZEP_API_KEY`,
    `FIREWORKS_API_KEY` and `INSFORGE_*`, **or** `MEDTRACE_LOCAL_MOCK=1` for an
    offline file-backed data layer. Clinical data routes return 503 without
    either.
  - *Imaging*: DICOM upload, MedSAM2 segmentation, draft reports (Qwen VL via
    Nebius). **Runs fully in mock mode with no secrets** — the easiest path to a
    working end-to-end demo.
- **`apps/web/`** (Vite 6 + React 19 + Tailwind v4, port 3000) — one app, three
  routes: `/` + `/patients/:id` (dashboard), `/imaging` (DICOM viewer),
  `/session` (voice consultation, lazy-loaded).
- **`services/transcription/`** — preserved prototype backing `/session`: a
  LangGraph backend (8010) behind a CopilotKit Express runtime (4000). Started
  separately with `npm run dev:transcription`; uses **OpenAI directly**
  (`OPENAI_API_KEY`, Whisper + `tts-1`), not Fireworks.

Depth references: `README.md` (Zep/ingest flows), `CLAUDE.md` (architecture +
gotchas), `DBMS-design.md` (InsForge Postgres schema).

### Monorepo layout

```
apps/
  api/               FastAPI service — clinical + imaging (apps.api.main:app, 8001)
  web/               React/Vite UI — all three product surfaces (3000)
services/
  transcription/     Voice/CopilotKit prototype (backend 8010, runtime 4000)
src/medtrace_agent/  Shared package (Zep, ingest, agents, imaging, InsForge, ontology)
tests/               Pytest suite (top-level files + tests/unit/)
migrations/          InsForge SQL migrations (schema, RLS, metadata, demo-mode)
mock/patient_data/   Mock patient JSON fixtures (seed source for local mock)
data/                Runtime data (local_mock store, notes, studies; mostly gitignored)
scripts/             Ontology apply, note ingest, seeding, local-mock reset, model probe
```

## Setup, build and run

### First-time setup

```bash
# One shared venv at ./.venv. Use .venv/bin/python for all Python work + pytest.
python -m venv .venv
.venv/bin/pip install -e ".[dev,imaging]"

npm install                    # root helper (concurrently)
npm --prefix apps/web ci

cp .env.example .env           # fill secrets, or set MEDTRACE_LOCAL_MOCK=1
```

`requirements.txt` is just `-e .[dev,imaging]` with comments — the editable
install is the canonical path. Optional extras: `.[medgemma-local]`
(torch/transformers, only needed for `MEDGEMMA_MODEL_ID`);
`services/transcription/backend/requirements.txt` for the `/session` prototype.

### Running

Prefer the root `package.json` scripts over re-deriving commands.

| Script | Starts | Ports |
|--------|--------|-------|
| `npm run dev` | api + web | 8001, 3000 |
| `npm run dev:api` | FastAPI only | 8001 |
| `npm run dev:web` | Vite only | 3000 |
| `npm run dev:transcription` | transcription backend + CopilotKit runtime | 8010, 4000 |

Running the backend manually:

```bash
.venv/bin/uvicorn apps.api.main:app --host 127.0.0.1 --port 8001 --reload
```

## Tests, lint, type checks

- Python: `.venv/bin/pytest -m "not integration"` (or `npm run test:py`) —
  currently 49 passed, 2 deselected. The `integration` marker hits the live NCBI
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
| `ingest/documents.py`, `ingest/scan_extract.py` | PDF → text (VLM page images or `pypdf`), `chunk_for_zep` → `graph.add(type="text")`. |
| `ontology/clinical.py` | Clinical entity/edge ontology; `auto_apply_clinical_ontology` runs at API startup. |
| `imaging/` | `storage.py` (study paths), `dicom.py` (preview render), `model_adapters/` (MedSAM2 + report). |
| `integrations/pubmed.py` | NCBI E-utilities (`esearch`/`esummary` JSON, not scraping). |
| `insforge_api.py` | InsForge registry; `@local_mock_fallback` routes each call to `local_store` when local mock is on. |
| `local_store.py` | File-backed InsForge stand-in (`data/local_mock/store.json` + `files/`). |
| `fireworks_config.py` | `fireworks_chat_client(...)` — the single `ChatOpenAI` construction point. |
| `env.py` | `load_repo_env()` — loads `.env` then `.env.local`, both `override=True`. |
| `patient_json.py`, `tracing.py` | Demo fixtures + `derive_age`/`derive_primary_doctor`; Langtrace/LangSmith init. |

**Zep model (central concept):** a patient is a Zep **user** (`zep_user_id`).
Short dialog + rolling context lives on **threads** (`thread.get_user_context`,
`thread.get`, `thread.add_messages`); durable episodes/facts/ontology live on the
**graph** (`graph.add`, `graph.search`, `graph.set_ontology`). New thread = new
conversation, same user.

**Ontology is a startup dependency.** `apps/api` applies it in its lifespan hook
(`AUTO_APPLY_ZEP_ONTOLOGY=true` by default); `routers/clinical.py` searches by
those custom node labels and edge types. Skip registration and the clinical
endpoints return empty arrays with a 200 — a silent failure. Ontology failures
are logged, not raised, so imaging + health still come up without Zep.
`scripts/apply_ontology.py` re-applies it manually.

**LLM layer:** every call goes through `fireworks_chat_client()` against an
**OpenAI-compatible** endpoint — Fireworks AI by default (`FIREWORKS_BASE_URL`,
`FIREWORKS_MODEL`, `FIREWORKS_VL_MODEL`). `FIREWORKS_VLM_API` picks the vision
transport: `chat` (`/v1/chat/completions`, default) vs `completions`
(`<image>`-prompt style). `FIREWORKS_REASONING_EFFORT=none` keeps Qwen3-style
CoT out of `reasoning_content` so JSON/text lands in `content`. Any
OpenAI-compatible endpoint works if you repoint the env vars (base URL,
model id, and key) — including a self-hosted vLLM server.

### API (`apps/api/`)

Single-demo-profile mode: `INSFORGE_PROFILE_ID` (a `public.profiles.id` uuid) is
required unless local mock is on. Routers: `patients`, `documents`, `threads`,
`clinical`, `studies`. `GET /api/health` reports `insforge_configured`,
`local_mock`, `fireworks_configured`, `zep_configured`, `demo_profile_id_set`
and an `imaging` block. Clinical data routes 503 without InsForge/local-mock;
imaging routes never do (they degrade to mock). Repo-root `data/` is mounted at
`/data` for study previews and mask overlays.

**No SQL mirror of clinical data** — `routers/clinical.py` derives conditions,
medications, labs, alerts and timeline per request from Zep (ontology search
plus regex over episode text). InsForge only stores chart/document/session rows.
Schema in `migrations/*.sql`; seed with `scripts/seed_mock_patients.py`.
`GET /api/patients/{id}/snapshot` has two paths: a stored
`chart_subjects.metadata.clinical` payload is validated straight into the
response (local-mock path); otherwise it falls back to the derived Zep builders.

### Imaging model adapters (`src/medtrace_agent/imaging/model_adapters/`)

`MedSAM2Service` and `MedGemmaService` resolve a mode in order: **HTTP endpoint**
(`MEDSAM2_ENDPOINT` / `MEDGEMMA_ENDPOINT`) → **local adapter**
(`MEDSAM2_ADAPTER_MODULE` / `MEDGEMMA_MODEL_ID`) → **deterministic mock**.
Reports use Qwen VL via Nebius (`NEBIUS_API_KEY`, `NEBIUS_BASE_URL`,
`NEBIUS_QWEN_VL_MODEL`); deterministic mock without the key. DICOM previews:
pydicom with `RescaleSlope`/`RescaleIntercept` and windowing
(`WindowCenter`/`WindowWidth`); ROI prompts are normalized 0–1 and converted to
pixels server-side.

### Web app (`apps/web/`)

One design system (Tailwind v4 tokens in `src/index.css`, shadcn-style
primitives in `src/components/ui/`), one typed client (`src/lib/api.ts` +
`src/lib/imagingApi.ts`, same-origin by default — set `VITE_API_BASE_URL` only
for a cross-origin API), and `src/lib/types.ts` mirroring `apps/api/schemas.py`.
Route components live in `src/components/imaging/` and
`src/components/session/`; dashboard components at `src/components/` top level.
The session route is lazy-loaded because CopilotKit + tiptap add ~2 MB, and
carries its own `session.css`; other routes are pure Tailwind.

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
- **`local_store` twins**: every `@local_mock_fallback` function in
  `insforge_api.py` dispatches by name to `local_store`. Adding one without its
  signature-matching twin raises `AttributeError` only under
  `MEDTRACE_LOCAL_MOCK=1`.
- **Local mock still calls Zep/Fireworks for chat + ingest** — only the
  InsForge persistence layer is faked. Chat fails without keys even in mock
  mode.
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
  placeholders only. `INSFORGE_API_KEY` is server-side only — never expose it in
  frontend bundles (the frontend uses the public anon key). Same for
  `FIREWORKS_API_KEY`, `ZEP_API_KEY`, `NEBIUS_API_KEY`, `OPENAI_API_KEY`.
- **Patient-data hygiene**: `data/` (local mock store, uploaded studies, notes)
  is largely gitignored on purpose — `data/studies/`, `data/local_mock/`,
  `data/**/*.pdf|.txt|.png|.jpg`. Do not commit real or realistic patient data;
  `data/studies/` in particular holds uploaded DICOM/images. The committed
  fixtures in `mock/patient_data/` are synthetic (no PHI).
- **CORS** is configured via `API_CORS_ORIGINS` (defaults to localhost:3000
  variants) — don't widen it to `*` for the main API.
- **Demo-grade output**: vision ingest can misread numbers or hallucinate
  structured fields; agent output is non-diagnostic clinical decision support,
  not a medical device. Preserve those disclaimers in code and UI.
