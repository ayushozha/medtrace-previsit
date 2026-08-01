-- Row Level Security for Medtrace app tables (owner = auth.uid() via profile_id / owner_profile_id).

ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS profiles_select_own ON public.profiles;
DROP POLICY IF EXISTS profiles_insert_own ON public.profiles;
DROP POLICY IF EXISTS profiles_update_own ON public.profiles;
DROP POLICY IF EXISTS profiles_delete_own ON public.profiles;

CREATE POLICY profiles_select_own ON public.profiles
  FOR SELECT TO authenticated
  USING (id = auth.uid());

CREATE POLICY profiles_insert_own ON public.profiles
  FOR INSERT TO authenticated
  WITH CHECK (id = auth.uid());

CREATE POLICY profiles_update_own ON public.profiles
  FOR UPDATE TO authenticated
  USING (id = auth.uid())
  WITH CHECK (id = auth.uid());

CREATE POLICY profiles_delete_own ON public.profiles
  FOR DELETE TO authenticated
  USING (id = auth.uid());

ALTER TABLE public.chart_subjects ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS chart_subjects_select_own ON public.chart_subjects;
DROP POLICY IF EXISTS chart_subjects_insert_own ON public.chart_subjects;
DROP POLICY IF EXISTS chart_subjects_update_own ON public.chart_subjects;
DROP POLICY IF EXISTS chart_subjects_delete_own ON public.chart_subjects;

CREATE POLICY chart_subjects_select_own ON public.chart_subjects
  FOR SELECT TO authenticated
  USING (owner_profile_id = auth.uid());

CREATE POLICY chart_subjects_insert_own ON public.chart_subjects
  FOR INSERT TO authenticated
  WITH CHECK (owner_profile_id = auth.uid());

CREATE POLICY chart_subjects_update_own ON public.chart_subjects
  FOR UPDATE TO authenticated
  USING (owner_profile_id = auth.uid())
  WITH CHECK (owner_profile_id = auth.uid());

CREATE POLICY chart_subjects_delete_own ON public.chart_subjects
  FOR DELETE TO authenticated
  USING (owner_profile_id = auth.uid());

ALTER TABLE public.chat_sessions ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS chat_sessions_select_own ON public.chat_sessions;
DROP POLICY IF EXISTS chat_sessions_insert_own ON public.chat_sessions;
DROP POLICY IF EXISTS chat_sessions_update_own ON public.chat_sessions;
DROP POLICY IF EXISTS chat_sessions_delete_own ON public.chat_sessions;

CREATE POLICY chat_sessions_select_own ON public.chat_sessions
  FOR SELECT TO authenticated
  USING (profile_id = auth.uid());

CREATE POLICY chat_sessions_insert_own ON public.chat_sessions
  FOR INSERT TO authenticated
  WITH CHECK (profile_id = auth.uid());

CREATE POLICY chat_sessions_update_own ON public.chat_sessions
  FOR UPDATE TO authenticated
  USING (profile_id = auth.uid())
  WITH CHECK (profile_id = auth.uid());

CREATE POLICY chat_sessions_delete_own ON public.chat_sessions
  FOR DELETE TO authenticated
  USING (profile_id = auth.uid());

ALTER TABLE public.documents ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS documents_select_own ON public.documents;
DROP POLICY IF EXISTS documents_insert_own ON public.documents;
DROP POLICY IF EXISTS documents_update_own ON public.documents;
DROP POLICY IF EXISTS documents_delete_own ON public.documents;

CREATE POLICY documents_select_own ON public.documents
  FOR SELECT TO authenticated
  USING (profile_id = auth.uid());

CREATE POLICY documents_insert_own ON public.documents
  FOR INSERT TO authenticated
  WITH CHECK (profile_id = auth.uid());

CREATE POLICY documents_update_own ON public.documents
  FOR UPDATE TO authenticated
  USING (profile_id = auth.uid())
  WITH CHECK (profile_id = auth.uid());

CREATE POLICY documents_delete_own ON public.documents
  FOR DELETE TO authenticated
  USING (profile_id = auth.uid());
