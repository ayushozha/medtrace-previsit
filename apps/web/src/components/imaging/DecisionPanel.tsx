import type { ReactNode } from 'react';
import {
  Brain,
  CheckCircle2,
  FileImage,
  Loader2,
  MessageSquareText,
  ShieldCheck,
  Sparkles,
  XCircle,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import type { ImagingStatus, Study } from '@/lib/types';

function ReportSection({
  icon: Icon,
  title,
  children,
}: {
  icon: typeof Brain;
  title: string;
  children: ReactNode;
}) {
  return (
    <article className="rounded-md border border-slate-800 bg-[#0c1420] p-4">
      <div className="mb-3 flex items-center gap-2">
        <span className="grid h-7 w-7 place-items-center rounded-md border border-slate-700 bg-slate-950 text-slate-300">
          <Icon className="h-4 w-4" />
        </span>
        <h3 className="text-sm font-semibold text-white">{title}</h3>
      </div>
      <p className="whitespace-pre-wrap text-sm leading-6 text-slate-300">{children}</p>
    </article>
  );
}

interface DecisionPanelProps {
  study: Study;
  imagingStatus: ImagingStatus;
  canRunReport: boolean;
  onRunReport: () => void;
  onAccept: () => void;
  onNeedsCorrection: () => void;
}

export function DecisionPanel({
  study,
  imagingStatus,
  canRunReport,
  onRunReport,
  onAccept,
  onNeedsCorrection,
}: DecisionPanelProps) {
  const reporting = study.status === 'reporting';

  return (
    <aside className="flex min-h-0 flex-col border-l border-slate-800 bg-[#080d15] max-lg:min-h-[640px]">
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4">
        <article className="rounded-md border border-slate-800 bg-[#0c1420] p-4">
          <div className="mb-3 flex items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <span className="grid h-8 w-8 place-items-center rounded-md border border-cyan-400/25 bg-cyan-400/10 text-cyan-200">
                {reporting ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Sparkles className="h-4 w-4" />
                )}
              </span>
              <h3 className="text-sm font-semibold text-white">Qwen VL Report</h3>
            </div>
            <button
              className="rounded-md border border-cyan-400/25 bg-cyan-400/10 px-3 py-1.5 text-xs font-semibold text-cyan-100 transition hover:bg-cyan-400/15 disabled:cursor-wait disabled:opacity-70"
              type="button"
              disabled={reporting || !canRunReport}
              onClick={onRunReport}
            >
              {reporting ? 'Generating' : 'Generate'}
            </button>
          </div>

          <p
            className={cn(
              'mb-3 text-xs font-medium',
              imagingStatus.nebius_configured ? 'text-emerald-300' : 'text-amber-300',
            )}
          >
            {imagingStatus.nebius_configured
              ? `Backend model ready: ${imagingStatus.model}`
              : 'Backend is in mock mode. Set NEBIUS_API_KEY in the repo .env and restart the API.'}
          </p>
          <p className="text-sm leading-6 text-slate-300">{study.report.summary}</p>

          <div className="mt-3">
            <div className="mb-2 flex items-center justify-between text-xs">
              <span className="text-slate-500">Draft confidence</span>
              <span className="font-semibold text-cyan-100">
                {Math.round(study.report.confidence * 100)}%
              </span>
            </div>
            <div className="h-2 overflow-hidden rounded-full bg-slate-800">
              <div
                className="h-full rounded-full bg-gradient-to-r from-cyan-400 to-emerald-400"
                style={{ width: `${study.report.confidence * 100}%` }}
              />
            </div>
          </div>
        </article>

        <ReportSection icon={FileImage} title="Findings">
          {study.report.findings}
        </ReportSection>
        <ReportSection icon={Brain} title="Impression">
          {study.report.impression}
        </ReportSection>
        <ReportSection icon={ShieldCheck} title="Recommendation">
          {study.report.recommendation}
        </ReportSection>

        {study.reviewNote && (
          <article className="rounded-md border border-amber-300/35 bg-amber-400/5 p-4">
            <div className="mb-2 flex items-center gap-2">
              <MessageSquareText className="h-4 w-4 text-amber-200" />
              <h3 className="text-sm font-semibold text-amber-100">Correction note</h3>
            </div>
            <p className="whitespace-pre-wrap text-sm leading-6 text-amber-100/80">
              {study.reviewNote}
            </p>
          </article>
        )}
      </div>

      <div className="grid shrink-0 gap-3 border-t border-slate-800 bg-[#090f18] p-4">
        <button
          className={cn(
            'flex w-full items-center justify-center gap-2 rounded-md px-4 py-3 text-sm font-semibold text-white transition',
            study.reviewDecision === 'accepted'
              ? 'bg-emerald-500 ring-2 ring-emerald-300/40'
              : 'bg-emerald-600 hover:bg-emerald-500',
          )}
          type="button"
          onClick={onAccept}
        >
          <CheckCircle2 className="h-4 w-4" />
          {study.reviewDecision === 'accepted' ? 'Draft Accepted' : 'Doctor Accepts Draft'}
        </button>
        <button
          className={cn(
            'flex w-full items-center justify-center gap-2 rounded-md px-4 py-3 text-sm font-semibold text-white transition',
            study.reviewDecision === 'needs-correction'
              ? 'bg-red-500 ring-2 ring-red-300/40'
              : 'bg-red-600 hover:bg-red-500',
          )}
          type="button"
          onClick={onNeedsCorrection}
        >
          <XCircle className="h-4 w-4" />
          Needs Correction
        </button>
      </div>
    </aside>
  );
}
