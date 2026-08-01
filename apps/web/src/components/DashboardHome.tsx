import { useState } from 'react';
import {
  AlertTriangle,
  ArrowLeft,
  Check,
  FileText,
  HeartPulse,
  Loader2,
  RefreshCw,
  Sparkles,
  Stethoscope,
  TriangleAlert,
} from 'lucide-react';
import { Timeline } from './Timeline';
import { LabTrends } from './LabTrends';
import { DocumentLibrary } from './DocumentLibrary';
import { AIChatPanel } from './AIChatPanel';
import { useSnapshot } from '@/hooks/useSnapshot';
import type { ClinicalSnapshot, RiskLevel } from '@/lib/types';

interface DashboardHomeProps {
  patientId: string;
  onBack?: () => void;
}

const priorityClass: Record<RiskLevel, string> = {
  High: 'border-red-200 bg-red-50 text-red-800',
  Medium: 'border-yellow-200 bg-yellow-50 text-yellow-900',
  Low: 'border-slate-200 bg-slate-50 text-slate-700',
};

export function DashboardHome({ patientId, onBack }: DashboardHomeProps) {
  const { snapshot, loading, error, refresh } = useSnapshot(patientId);

  if (loading && !snapshot) {
    return <FullScreenStatus icon={<Loader2 size={20} className="animate-spin" />} text="Loading patient chart..." onBack={onBack} />;
  }

  if (error && !snapshot) {
    return (
      <FullScreenStatus
        icon={<TriangleAlert size={20} />}
        text={`Could not load patient chart: ${error}`}
        onBack={onBack}
        action={
          <button
            type="button"
            onClick={() => void refresh()}
            className="mt-3 inline-flex items-center gap-2 rounded-md border border-slate-200 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 hover:bg-slate-50"
          >
            <RefreshCw size={12} /> Retry
          </button>
        }
      />
    );
  }

  if (!snapshot) {
    return <FullScreenStatus icon={<TriangleAlert size={20} />} text="Patient not found." onBack={onBack} />;
  }

  return <DashboardBody snapshot={snapshot} onBack={onBack} onRefresh={() => void refresh()} />;
}

function DashboardBody({
  snapshot,
  onBack,
  onRefresh,
}: {
  snapshot: ClinicalSnapshot;
  onBack?: () => void;
  onRefresh: () => void;
}) {
  const { patient } = snapshot;
  const sexLabel = patient.sex === 'M' ? 'Male' : patient.sex === 'F' ? 'Female' : 'Other';
  const patientMeta: Array<[string, string]> = [
    ['Primary doctor', patient.primary_doctor ?? 'Not assigned'],
    ['Last visit', patient.last_visit ?? '-'],
    ['Documents', `${patient.document_count} uploaded`],
    ['DOB', patient.dob ?? '-'],
  ];

  // Interactive review checklist, keyed by item text so a snapshot refresh keeps state.
  const [checkedItems, setCheckedItems] = useState<ReadonlySet<string>>(new Set());
  const toggleChecklistItem = (item: string) => {
    setCheckedItems((prev) => {
      const next = new Set(prev);
      if (next.has(item)) {
        next.delete(item);
      } else {
        next.add(item);
      }
      return next;
    });
  };

  return (
    // xl layout: flex row with `gap-4` (1rem) between the dashboard column
    // and the chat panel. The chat panel is a sticky flex sibling (no longer
    // position:fixed) so the gap is exactly 16px regardless of viewport width.
    <div className="mx-auto min-h-screen w-full max-w-[1440px] px-4 py-4 sm:px-6 lg:px-8">
      <div className="xl:flex xl:items-start xl:gap-4">
      <div className="min-w-0 xl:flex-1">
      <header className="clinical-panel mb-4 overflow-hidden">
        <div className="flex flex-col gap-4 p-4 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex min-w-0 items-center gap-4">
            {onBack && (
              <button
                onClick={onBack}
                className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-border bg-slate-50 text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-900"
                title="Back to Patient List"
              >
                <ArrowLeft size={16} />
              </button>
            )}
            <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-primary text-sm font-semibold text-primary-foreground shadow-sm">
              {patient.name
                .split(' ')
                .map((name) => name[0])
                .filter(Boolean)
                .slice(0, 2)
                .join('')
                .toUpperCase()}
            </div>
            <div className="min-w-0">
              <div className="mb-1 flex flex-wrap items-center gap-2">
                <h1 className="truncate text-2xl font-semibold leading-8 text-slate-900">{patient.name}</h1>
                <span className="rounded-md border border-slate-200 bg-slate-50 px-2 py-1 font-mono text-[11px] font-semibold text-slate-500">
                  {patient.zep_user_id}
                </span>
              </div>
              <p className="text-xs font-medium uppercase tracking-[0.12em] text-slate-500">
                {patient.age || '?'}Y - {sexLabel} - Updated {patient.last_updated?.slice(0, 10) ?? '-'}
              </p>
            </div>
          </div>

          <div className="grid gap-2 sm:grid-cols-4 lg:w-[540px]">
            {patientMeta.map(([label, value]) => (
              <div key={label} className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
                <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-slate-400">{label}</p>
                <p className="mt-1 truncate text-xs font-semibold text-slate-800">{value}</p>
              </div>
            ))}
          </div>
        </div>
      </header>

      <main className="min-w-0 space-y-4">
          <section className="clinical-panel p-4">
            <div className="mb-4 flex items-start justify-between gap-4">
              <div>
                <h2 className="clinical-section-title">AI Patient Summary</h2>
                <p className="mt-2 max-w-5xl text-sm leading-6 text-slate-700">
                  {patient.summary ??
                    'No summary yet. Upload documents or send a chat message — Zep will start populating memory.'}
                </p>
              </div>
              <button
                type="button"
                onClick={onRefresh}
                title="Refresh"
                className="hidden rounded-lg border border-blue-100 bg-blue-50 px-3 py-2 text-primary md:block"
              >
                <Sparkles size={18} />
              </button>
            </div>
            <div className="grid gap-3 border-t border-slate-100 pt-4 md:grid-cols-3">
              {snapshot.insights.length > 0 ? (
                snapshot.insights.map((insight) => (
                  <article key={insight.title} className="rounded-lg border border-slate-200 bg-white p-3">
                    <div className="mb-2 flex items-center justify-between gap-2">
                      <h3 className="text-sm font-semibold text-slate-900">{insight.title}</h3>
                      <span
                        className={`rounded-md border px-2 py-1 text-[10px] font-semibold ${priorityClass[insight.priority]}`}
                      >
                        {insight.priority}
                      </span>
                    </div>
                    <p className="text-xs leading-5 text-slate-600">{insight.detail}</p>
                    {insight.evidence.length > 0 && (
                      <p className="mt-2 text-[10px] font-semibold uppercase tracking-[0.12em] text-slate-400">
                        Evidence linked
                      </p>
                    )}
                  </article>
                ))
              ) : (
                <EmptyMessage text="No evidence-linked insights yet. Ingest a document to see them appear." />
              )}
            </div>
          </section>

          <section className="clinical-panel overflow-hidden">
            <div className="border-b border-border bg-slate-50 px-4 py-3">
              <h2 className="clinical-section-title">Current Clinical Snapshot</h2>
            </div>
            <div className="grid gap-3 p-4 md:grid-cols-2 2xl:grid-cols-4">
              <SnapshotCard title="Active Conditions" icon={<Stethoscope size={14} />}>
                {snapshot.active_conditions.length > 0 ? (
                  snapshot.active_conditions.map((condition) => (
                    <div key={condition.name} className="rounded-md bg-slate-100 px-2 py-1.5">
                      <p className="text-xs font-semibold text-slate-800">{condition.name}</p>
                      <p className="text-[10px] text-slate-500">{condition.first_seen ?? condition.status}</p>
                    </div>
                  ))
                ) : (
                  <SnapshotEmpty />
                )}
              </SnapshotCard>

              <SnapshotCard title="Current Medications" icon={<HeartPulse size={14} />}>
                {snapshot.current_medications.length > 0 ? (
                  snapshot.current_medications
                    .filter((m) => m.status === 'Active')
                    .map((medication) => (
                      <div key={medication.name} className="rounded-md bg-blue-50 px-2 py-1.5">
                        <p className="text-xs font-semibold text-slate-800">{medication.name}</p>
                        <p className="text-[10px] text-slate-500">
                          {medication.dose ?? '?'} - {medication.frequency ?? '?'}
                        </p>
                      </div>
                    ))
                ) : (
                  <SnapshotEmpty />
                )}
              </SnapshotCard>

              <SnapshotCard title="Allergies">
                {snapshot.allergies.length > 0 ? (
                  snapshot.allergies.map((allergy) => (
                    <div
                      key={allergy.allergen}
                      className="rounded-md border border-red-100 bg-red-50 px-2 py-1.5"
                    >
                      <p className="text-xs font-semibold text-red-800">
                        {allergy.allergen}
                        {allergy.reaction ? `: ${allergy.reaction}` : ''}
                      </p>
                      <p className="text-[10px] text-red-500">Source: {allergy.source ?? 'Zep memory'}</p>
                    </div>
                  ))
                ) : (
                  <SnapshotEmpty />
                )}
              </SnapshotCard>

              <SnapshotCard title="Recent Abnormal">
                {snapshot.recent_abnormal.length > 0 ? (
                  snapshot.recent_abnormal.map((finding) => (
                    <div
                      key={finding.test}
                      className="rounded-md border border-yellow-100 bg-yellow-50 px-2 py-1.5"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-xs font-semibold text-slate-800">{finding.test}</span>
                        <span className="rounded bg-yellow-100 px-1.5 py-0.5 font-mono text-[10px] text-yellow-900">
                          {finding.value}
                        </span>
                      </div>
                      <p className="mt-1 text-[10px] text-yellow-800">{finding.source ?? 'Zep memory'}</p>
                    </div>
                  ))
                ) : (
                  <SnapshotEmpty />
                )}
              </SnapshotCard>
            </div>
          </section>

          <div>
            <LabTrends labs={snapshot.lab_trends} />
          </div>

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_330px]">
            <Timeline timeline={snapshot.timeline} onRefresh={onRefresh} />
            <div className="space-y-4">
              <section className="clinical-panel p-4">
                <div className="mb-4 flex items-center justify-between">
                  <h2 className="clinical-section-title">Do Not Miss</h2>
                  <AlertTriangle size={16} className="text-red-500" />
                </div>
                <div className="space-y-2">
                  {snapshot.risk_alerts.length > 0 ? (
                    snapshot.risk_alerts.map((alert) => (
                      <div
                        key={alert.message}
                        className={`rounded-lg border p-3 ${priorityClass[alert.priority]}`}
                      >
                        <p className="mb-1 text-xs font-semibold leading-5">{alert.message}</p>
                        <p className="text-[10px] font-semibold uppercase tracking-[0.12em] opacity-70">
                          {alert.evidence ?? 'Zep edge'}
                        </p>
                      </div>
                    ))
                  ) : (
                    <SnapshotEmpty text="Nothing flagged. Add documents to populate alerts." />
                  )}
                </div>
              </section>

              <DocumentLibrary
                documents={snapshot.documents}
                patientId={patient.id}
                onUploaded={onRefresh}
              />
            </div>
          </div>

          <div className="grid grid-cols-1 gap-4 2xl:grid-cols-[minmax(0,1fr)_380px]">
            <section className="clinical-panel overflow-hidden">
              <div className="border-b border-border bg-slate-50 px-4 py-3">
                <h2 className="clinical-section-title">Medication History</h2>
              </div>
              <div className="overflow-x-auto p-4">
                <table className="w-full text-left text-xs">
                  <thead className="border-b border-slate-100 text-[10px] uppercase tracking-[0.12em] text-slate-500">
                    <tr>
                      <th className="pb-2 font-semibold">Medication</th>
                      <th className="pb-2 font-semibold">Dose</th>
                      <th className="pb-2 font-semibold">Status</th>
                      <th className="pb-2 font-semibold">Period</th>
                    </tr>
                  </thead>
                  <tbody>
                    {snapshot.current_medications.length > 0 ? (
                      snapshot.current_medications.map((medication) => (
                        <tr key={medication.name} className="border-b border-slate-50 last:border-0">
                          <td className="py-3 font-semibold text-slate-800">{medication.name}</td>
                          <td className="py-3 text-slate-600">{medication.dose ?? '-'}</td>
                          <td className="py-3">
                            <span
                              className={`rounded-md border px-2 py-1 text-[10px] font-semibold ${
                                medication.status === 'Active'
                                  ? 'border-green-200 bg-green-50 text-green-700'
                                  : 'border-slate-200 bg-slate-50 text-slate-600'
                              }`}
                            >
                              {medication.status}
                            </span>
                          </td>
                          <td className="py-3 text-slate-500">
                            {(medication.start ?? '-') + ' - ' + (medication.end ?? '-')}
                          </td>
                        </tr>
                      ))
                    ) : (
                      <tr>
                        <td colSpan={4} className="py-4 text-center text-[11px] text-slate-400">
                          No medications recorded yet.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </section>

            <section className="rounded-xl border border-slate-800 bg-slate-950 p-4 shadow-[0_1px_3px_0_rgb(15_23_42_/_0.1)]">
              <div className="mb-3 flex items-center justify-between">
                <h2 className="clinical-section-title text-slate-400">Suggested Review Checklist</h2>
                <FileText size={16} className="text-blue-300" />
              </div>
              <div className="grid gap-3">
                {snapshot.doctor_checklist.map((item) => {
                  const checked = checkedItems.has(item);
                  return (
                    <label key={item} className="group flex cursor-pointer items-start gap-3">
                      <input
                        type="checkbox"
                        className="sr-only"
                        checked={checked}
                        onChange={() => toggleChecklistItem(item)}
                      />
                      <div
                        className={`mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded border transition-all duration-200 ${
                          checked ? 'border-blue-500 bg-blue-500' : 'border-slate-700 group-hover:border-blue-500'
                        }`}
                      >
                        {checked ? (
                          <Check size={12} className="scale-100 text-white transition-transform duration-200" />
                        ) : (
                          <div className="h-2 w-2 rounded-sm bg-blue-500 opacity-0 transition-opacity group-hover:opacity-30" />
                        )}
                      </div>
                      <span
                        className={`text-[12px] leading-5 transition-colors duration-200 ${
                          checked ? 'text-slate-500 line-through' : 'text-slate-300'
                        }`}
                      >
                        {item}
                      </span>
                    </label>
                  );
                })}
              </div>
            </section>
          </div>
        </main>
      </div>

        <aside className="mt-4 xl:sticky xl:top-4 xl:mt-0 xl:w-[480px] xl:shrink-0 xl:self-start">
          <div className="flex h-[560px] flex-col overflow-hidden rounded-xl border border-border bg-white shadow-[0_1px_3px_0_rgb(15_23_42_/_0.08)] xl:h-[calc(100vh-2rem)]">
            <AIChatPanel
              patientId={patient.id}
              patientName={patient.name}
              primaryDoctor={patient.primary_doctor ?? 'Doctor'}
              onUploaded={onRefresh}
            />
          </div>
        </aside>
      </div>
    </div>
  );
}

function FullScreenStatus({
  icon,
  text,
  action,
  onBack,
}: {
  icon: React.ReactNode;
  text: string;
  action?: React.ReactNode;
  onBack?: () => void;
}) {
  return (
    <div className="mx-auto flex min-h-screen w-full max-w-[1440px] flex-col px-4 py-4 sm:px-6 lg:px-8">
      {onBack && (
        <button
          onClick={onBack}
          className="mb-4 inline-flex h-9 w-9 items-center justify-center self-start rounded-full border border-border bg-white text-slate-500 transition-colors hover:bg-slate-50 hover:text-slate-900"
        >
          <ArrowLeft size={16} />
        </button>
      )}
      <div className="grid flex-1 place-items-center">
        <div className="flex flex-col items-center gap-2 text-sm text-slate-500">
          {icon}
          <p className="max-w-md text-center">{text}</p>
          {action}
        </div>
      </div>
    </div>
  );
}

function SnapshotCard({
  title,
  icon,
  children,
}: {
  title: string;
  icon?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-3">
      <h3 className="mb-3 flex items-center gap-2 text-[12px] font-semibold text-slate-500">
        {icon}
        {title}
      </h3>
      <div className="space-y-2">{children}</div>
    </div>
  );
}

function SnapshotEmpty({ text = 'No data yet' }: { text?: string }) {
  return <p className="rounded-md bg-slate-50 px-2 py-1.5 text-[11px] text-slate-400">{text}</p>;
}

function EmptyMessage({ text }: { text: string }) {
  return (
    <div className="col-span-full rounded-md border border-dashed border-slate-200 bg-slate-50 px-4 py-3 text-center text-xs text-slate-500">
      {text}
    </div>
  );
}
