-- Medtrace app persistence (InsForge Postgres): profiles, chart_subjects, chat_sessions, documents
-- See DBMS-design.md. Zep / ontology not mirrored here.

CREATE TABLE IF NOT EXISTS public.profiles (
  id uuid PRIMARY KEY REFERENCES auth.users (id) ON DELETE CASCADE,
  display_name text,
  role text,
  metadata jsonb NOT NULL DEFAULT '{}',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.chart_subjects (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_profile_id uuid NOT NULL REFERENCES public.profiles (id) ON DELETE CASCADE,
  zep_user_id text NOT NULL,
  display_name text,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT chart_subjects_owner_zep_unique UNIQUE (owner_profile_id, zep_user_id)
);

CREATE INDEX IF NOT EXISTS idx_chart_subjects_owner ON public.chart_subjects (owner_profile_id);
CREATE INDEX IF NOT EXISTS idx_chart_subjects_zep ON public.chart_subjects (zep_user_id);

CREATE TABLE IF NOT EXISTS public.chat_sessions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  profile_id uuid NOT NULL REFERENCES public.profiles (id) ON DELETE CASCADE,
  chart_subject_id uuid REFERENCES public.chart_subjects (id) ON DELETE SET NULL,
  zep_thread_id text NOT NULL,
  title text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT chat_sessions_zep_thread_unique UNIQUE (zep_thread_id)
);

CREATE INDEX IF NOT EXISTS idx_chat_sessions_profile ON public.chat_sessions (profile_id);
CREATE INDEX IF NOT EXISTS idx_chat_sessions_chart ON public.chat_sessions (chart_subject_id);

CREATE TABLE IF NOT EXISTS public.documents (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  doc_id text NOT NULL,
  profile_id uuid NOT NULL REFERENCES public.profiles (id) ON DELETE CASCADE,
  chart_subject_id uuid REFERENCES public.chart_subjects (id) ON DELETE SET NULL,
  filename text NOT NULL,
  document_kind text NOT NULL CHECK (
    document_kind IN ('clinical_pdf', 'radiology_note', 'conversation_note')
  ),
  extract_mode text,
  episode_count integer,
  storage_bucket text NOT NULL,
  storage_key text NOT NULL,
  storage_url text,
  metadata jsonb NOT NULL DEFAULT '{}',
  uploaded_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT documents_doc_id_unique UNIQUE (doc_id)
);

CREATE INDEX IF NOT EXISTS idx_documents_profile ON public.documents (profile_id);
CREATE INDEX IF NOT EXISTS idx_documents_chart ON public.documents (chart_subject_id);
CREATE INDEX IF NOT EXISTS idx_documents_kind ON public.documents (document_kind);
CREATE INDEX IF NOT EXISTS idx_documents_chart_kind ON public.documents (chart_subject_id, document_kind);
