import { useEffect, useState } from 'react';
import { Loader2, Mic, ShieldCheck, TriangleAlert } from 'lucide-react';

import { DashboardHome } from '@/components/DashboardHome';
import { PreVisitCheckinDialog } from '@/components/demo/PreVisitCheckinDialog';
import { Button } from '@/components/ui/button';
import { getDemoStatus, type DemoStatus } from '@/lib/demoApi';

const FINAL_PROMPT =
  'What changed today, what should the clinician verify, and what evidence supports it?';

export function YcMedplumHackathonDemo() {
  const [status, setStatus] = useState<DemoStatus | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    getDemoStatus(controller.signal).then(setStatus).catch((err: Error) => setStatusError(err.message));
    return () => controller.abort();
  }, []);

  const patientId = status?.demo_patient_id ?? null;

  if (!status && !statusError) {
    return (
      <div className="grid min-h-[70vh] place-items-center text-sm text-slate-500">
        <Loader2 className="h-5 w-5 animate-spin" />
      </div>
    );
  }

  if (!patientId) {
    return (
      <div className="mx-auto max-w-3xl px-4 py-12 sm:px-6">
        <section className="clinical-panel p-6">
          <div className="flex items-start gap-3">
            <TriangleAlert className="mt-0.5 h-5 w-5 shrink-0 text-amber-500" />
            <div>
              <h1 className="text-lg font-semibold text-slate-950">Demo patient is not available</h1>
              <p className="mt-2 text-sm leading-6 text-slate-600">
                {statusError ??
                  'Configure Medplum and provision the tagged synthetic demo Patient.'}
              </p>
            </div>
          </div>
        </section>
      </div>
    );
  }

  return (
    <>
      <div className="mx-auto w-full max-w-[1440px] px-4 pt-4 sm:px-6 lg:px-8">
        <div className="clinical-panel flex flex-col gap-3 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-center gap-3">
            <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-blue-50 text-primary">
              <ShieldCheck size={17} />
            </span>
            <div>
              <p className="clinical-section-title">YC Medplum hackathon flow</p>
              <p className="mt-1 text-xs text-slate-600">
                Existing chart → evidence-linked check-in → clinician approval → FHIR → eligibility
              </p>
            </div>
          </div>
          <span className="rounded-md border border-slate-200 bg-slate-50 px-2.5 py-1.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-slate-500">
            Video target 2:45 · hard stop before 3:00
          </span>
        </div>
      </div>

      <DashboardHome
        key={`${patientId}-${refreshKey}`}
        patientId={patientId}
        headerAction={
          <Button size="lg" onClick={() => setDialogOpen(true)}>
            <Mic size={14} /> Start pre-visit check-in
          </Button>
        }
        suggestedPrompts={[FINAL_PROMPT]}
      />

      <PreVisitCheckinDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        patientId={patientId}
        status={status}
        onSaved={() => setRefreshKey((value) => value + 1)}
      />
    </>
  );
}
