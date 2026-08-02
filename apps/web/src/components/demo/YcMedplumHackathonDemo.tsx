import { useEffect, useState } from 'react';
import { FileCheck2, Loader2, Mic, ScanLine, ShieldCheck, TriangleAlert } from 'lucide-react';
import { Link } from 'react-router-dom';

import { DashboardHome } from '@/components/DashboardHome';
import { PreVisitCheckinDialog } from '@/components/demo/PreVisitCheckinDialog';
import { Button, buttonVariants } from '@/components/ui/button';
import { getDemoStatus, type DemoStatus } from '@/lib/demoApi';
import { fetchStudies } from '@/lib/imagingApi';
import type { StudyUpload } from '@/lib/types';

const FINAL_PROMPT =
  'What changed today, what should the clinician verify, and what evidence supports it?';

export function YcMedplumHackathonDemo() {
  const [status, setStatus] = useState<DemoStatus | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [acceptedImaging, setAcceptedImaging] = useState<StudyUpload | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    getDemoStatus(controller.signal).then(setStatus).catch((err: Error) => setStatusError(err.message));
    return () => controller.abort();
  }, []);

  const patientId = status?.demo_patient_id ?? null;

  useEffect(() => {
    if (!patientId) return;
    const controller = new AbortController();
    fetchStudies(controller.signal, patientId)
      .then((studies) =>
        setAcceptedImaging(
          studies.find(
            (study) =>
              study.review_decision === 'accepted' && Boolean(study.report?.fhir_diagnostic_report_id),
          ) ?? null,
        ),
      )
      .catch(() => setAcceptedImaging(null));
    return () => controller.abort();
  }, [patientId]);

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
        <div className="clinical-panel flex flex-col gap-3 px-4 py-3 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex items-center gap-3">
            <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-blue-50 text-primary">
              <ShieldCheck size={17} />
            </span>
            <div>
              <p className="clinical-section-title">YC Medplum hackathon flow</p>
              <p className="mt-1 text-xs text-slate-600">
                Longitudinal chart → accepted 3D imaging → evidence-linked check-in → validated FHIR → eligibility
              </p>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2 text-[11px] text-slate-600">
            <span className="inline-flex items-center gap-1.5 rounded-md border border-slate-200 bg-slate-50 px-2.5 py-1.5">
              <FileCheck2 size={13} className={acceptedImaging ? 'text-emerald-600' : 'text-amber-500'} />
              {acceptedImaging
                ? `${acceptedImaging.modality} ${acceptedImaging.body_part} · DiagnosticReport/${acceptedImaging.report?.fhir_diagnostic_report_id}`
                : 'No clinician-accepted imaging report yet'}
            </span>
            <Link
              className={buttonVariants({ size: 'sm', variant: 'outline' })}
              to={`/patients/${patientId}/imaging`}
            >
              <ScanLine size={14} /> Open 3D imaging
            </Link>
          </div>
        </div>
      </div>

      <DashboardHome
        key={`${patientId}-${refreshKey}`}
        patientId={patientId}
        headerAction={
          <div className="flex flex-wrap gap-2">
            <Link
              className={buttonVariants({ size: 'lg', variant: 'outline' })}
              to={`/patients/${patientId}/imaging`}
            >
              <ScanLine size={14} /> Review 3D imaging
            </Link>
            <Button size="lg" onClick={() => setDialogOpen(true)}>
              <Mic size={14} /> Start pre-visit check-in
            </Button>
          </div>
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
