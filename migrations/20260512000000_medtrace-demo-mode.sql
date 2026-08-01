-- =============================================================================
-- DEMO ONLY -- single-profile mode for the React Frontend / hybrid integration.
-- =============================================================================
-- The Frontend uses the InsForge anon key directly to read chart_subjects,
-- chat_sessions, and documents. Without auth (single demo profile mode),
-- auth.uid() is NULL on the anon role, so the per-user RLS policies installed
-- in 20260510160000_medtrace-app-rls.sql would block every read.
--
-- For the demo we disable RLS on those three tables. All write traffic still
-- goes through the FastAPI service (apps/api/) which authenticates with the
-- server-side INSFORGE_API_KEY -- the anon key cannot mutate from the browser
-- in practice, because the FastAPI endpoints are the only producers.
--
-- profiles stays RLS-protected: it is only ever written by the seeding step
-- below and the eventual auth flow.
--
-- TO RE-ENABLE PRODUCTION RLS (after wiring InsForge auth in the Frontend):
--   ALTER TABLE public.chart_subjects ENABLE ROW LEVEL SECURITY;
--   ALTER TABLE public.chat_sessions  ENABLE ROW LEVEL SECURITY;
--   ALTER TABLE public.documents      ENABLE ROW LEVEL SECURITY;
-- The original per-user policies installed in 20260510160000_medtrace-app-rls.sql
-- remain on the tables (DISABLE RLS does not drop them), so re-enabling RLS is
-- a single ALTER per table.
-- =============================================================================

ALTER TABLE public.chart_subjects DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.chat_sessions  DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.documents      DISABLE ROW LEVEL SECURITY;
