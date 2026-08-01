-- Optional structured patient fields stored as JSON on chart_subjects (demo metadata only).
ALTER TABLE public.chart_subjects
  ADD COLUMN IF NOT EXISTS metadata jsonb NOT NULL DEFAULT '{}';
