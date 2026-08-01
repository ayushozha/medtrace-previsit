import { useState } from 'react';
import {
  AlertTriangle,
  ArrowLeft,
  Check,
  FileText,
  HeartPulse,
  Loader2,
  Pencil,
  RefreshCw,
  Sparkles,
  Stethoscope,
  TriangleAlert,
} from 'lucide-react';
import { Timeline } from './Timeline';
import { LabTrends } from './LabTrends';
import { DocumentLibrary } from './DocumentLibrary';
import { AIChatPanel } from './AIChatPanel';
import { DashboardCollabLayer } from './dashboard/DashboardCollabLayer';
import { useDashboardCollab } from './dashboard/DashboardCollabContext';
import { PatientModeSwitcher, PatientVisitLaunchpad } from './PatientVisitNav';
import { useSnapshot } from '@/hooks/useSnapshot';
import type { ClinicalSnapshot, RiskLevel } from '@/lib/types';
import type { Patient } from '@/lib/types';
import { apiPatch } from '@/lib/api';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';

interface DashboardHomeProps {
  patientId: string;
  onBack?: () => void;
  headerAction?: React.ReactNode;
  suggestedPrompts?: string[];
  /** When true, expects a parent CopilotKit provider (PatientChartWorkspace). */
  collabMode?: boolean;
}

const priorityClass: Record<RiskLevel, string> = {
  High: 'border-red-200 bg-red-50 text-red-800',
  Medium: 'border-yellow-200 bg-yellow-50 text-yellow-900',
  Low: 'border-slate-200 bg-slate-50 text-slate-700',
};

function EditPatientButton({ patient, onSaved }: { patient: Patient; onSaved: () => void }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(patient.name);
  const [dob, setDob] = useState(patient.dob ?? '');
  const [sex, setSex] = useState(patient.sex);
  const [doctor, setDoctor] = useState(patient.primary_doctor ?? '');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const openEditor = () => {
    setName(patient.name);
    setDob(patient.dob ?? '');
    setSex(patient.sex);
    setDoctor(patient.primary_doctor ?? '');
    setError('');
    setOpen(true);
  };

  const save = async () => {
    if (!name.trim()) return;
    setSaving(true);
    setError('');
    try {
      await apiPatch(`/api/patients/${patient.id}`, {
        display_name: name.trim(),
        dob: dob || null,
        sex,
        primary_doctor: doctor.trim() || null,
      });
      setOpen(false);
      onSaved();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Could not update the Medplum Patient.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <button
        type="button"
        onClick={openEditor}
        className="inline-flex items-center gap-1.5 rounded-md border border-slate-200 bg-white px-2.5 py-1.5 text-xs font-semibold text-slate-700 hover:bg-slate-50"
      >
        <Pencil size={12} /> Edit FHIR details
      </button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>Edit canonical patient</DialogTitle>
            <DialogDescription>These fields update the Medplum Patient resource directly.</DialogDescription>
          </DialogHeader>
          <div className="grid gap-3">
            <Input value={name} onChange={(event) => setName(event.target.value)} placeholder="Display name" />
            <div className="grid gap-3 sm:grid-cols-2">
              <Input type="date" value={dob} onChange={(event) => setDob(event.target.value)} />
              <select
                value={sex}
                onChange={(event) => setSex(event.target.value as Patient['sex'])}
                className="h-10 rounded-md border border-input bg-background px-3 text-sm"
              >
                <option value="O">Unknown / other</option>
                <option value="F">Female</option>
                <option value="M">Male</option>
              </select>
            </div>
            <Input value={doctor} onChange={(event) => setDoctor(event.target.value)} placeholder="Primary clinician" />
            {error && <p className="text-xs text-red-600">{error}</p>}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setOpen(false)} disabled={saving}>Cancel</Button>
            <Button onClick={() => void save()} disabled={saving || !name.trim()}>
              {saving ? 'Saving…' : 'Save in Medplum'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

export function DashboardHome({
  patientId,
  onBack,
  headerAction,
  suggestedPrompts,
  collabMode = false,
}: DashboardHomeProps) {
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

  const body = (
    <DashboardBody
      snapshot={snapshot}
      onBack={onBack}
      onRefresh={() => void refresh()}
      headerAction={headerAction}
      suggestedPrompts={suggestedPrompts}
    />
  );

  if (!collabMode) return body;

  return (
    <DashboardCollabLayer snapshot={snapshot} onRefresh={() => void refresh()}>
      {body}
    </DashboardCollabLayer>
  );
}

function DashboardBody({
  snapshot,
  onBack,
  onRefresh,
  headerAction,
  suggestedPrompts,
}: {
  snapshot: ClinicalSnapshot;
  onBack?: () => void;
  onRefresh: () => void;
  headerAction?: React.ReactNode;
  suggestedPrompts?: string[];
}) {
  const { patient } = snapshot;
  const sexLabel = patient.sex === 'M' ? 'Male' : patient.sex === 'F' ? 'Female' : 'Other';
  const patientMeta: Array<[string, string]> = [
    ['Primary doctor', patient.primary_doctor ?? 'Not assigned'],
    ['Last visit', patient.last_visit ?? '-'],
    ['Documents', `${patient.document_count} uploaded`],
    ['DOB', patient.dob ?? '-'],
  ];

  const collab = useDashboardCollab();
  const toggleChecklistItem = (item: { id: string; text: string; done: boolean; agent_note?: string | null }) => {
    void apiPatch(`/api/patients/${patient.id}/checklist/${item.id}`, {
      text: item.text,
      done: !item.done,
      agent_note: item.agent_note ?? null,
    }).then(onRefresh);
  };

  const focusKey = collab?.focus?.key?.toLowerCase() ?? '';
  const focusType = collab?.focus?.type;
  const isFocused = (type: string, name: string) =>
    !!focusKey && focusType === type && name.toLowerCase().includes(focusKey);

  return (
    // xl layout: flex row with `gap-4` (1rem) between the dashboard column
    // and the chat panel. The chat panel is a sticky flex sibling (no longer
    // position:fixed) so the gap is exactly 16px regardless of viewport width.
    <div>
      <PatientModeSwitcher patientId={patient.id} patientName={patient.name} active="chart" />
      <div className="mx-auto min-h-screen w-full max-w-[1440px] px-4 py-4 sm:px-6 lg:px-8">
      <div className="lg:flex lg:items-start lg:gap-4">
      <div className="min-w-0 lg:flex-1">
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

          <div className="flex flex-col items-stretch gap-2 lg:w-[540px]">
            <div className="flex justify-end gap-2">
              {headerAction}
              <EditPatientButton patient={patient} onSaved={onRefresh} />
            </div>
            <div className="grid gap-2 sm:grid-cols-4">
            {patientMeta.map(([label, value]) => (
              <div key={label} className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
                <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-slate-400">{label}</p>
                <p className="mt-1 truncate text-xs font-semibold text-slate-800">{value}</p>
              </div>
            ))}
            </div>
          </div>
        </div>
      </header>

      <main className="min-w-0 space-y-4">
          <PatientVisitLaunchpad patientId={patient.id} />

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
                    <div
                      key={condition.name}
                      className={`rounded-md px-2 py-1.5 ${
                        isFocused('condition', condition.name)
                          ? 'bg-amber-100 ring-2 ring-amber-400'
                          : 'bg-slate-100'
                      }`}
                    >
                      <p className="text-xs font-semibold text-slate-800">{condition.name}</p>
                      <p className="text-[10px] text-slate-500">{condition.first_seen ?? condition.status}</p>
                      <VerificationBadge status={condition.verification_status} />
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
                      <div
                        key={medication.name}
                        className={`rounded-md px-2 py-1.5 ${
                          isFocused('med', medication.name)
                            ? 'bg-amber-100 ring-2 ring-amber-400'
                            : 'bg-blue-50'
                        }`}
                      >
                        <p className="text-xs font-semibold text-slate-800">{medication.name}</p>
                        <p className="text-[10px] text-slate-500">
                          {medication.dose ?? '?'} - {medication.frequency ?? '?'}
                        </p>
                        <VerificationBadge status={medication.verification_status} />
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
                      <p className="text-[10px] text-red-500">Source: {allergy.source ?? 'FHIR chart'}</p>
                      <VerificationBadge status={allergy.verification_status} />
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
                      <p className="mt-1 text-[10px] text-yellow-800">{finding.source ?? 'FHIR chart'}</p>
                      <VerificationBadge status={finding.verification_status} />
                    </div>
                  ))
                ) : (
                  <SnapshotEmpty />
                )}
              </SnapshotCard>
            </div>
          </section>

          <div>
            <LabTrends
              labs={snapshot.lab_trends}
              highlightTest={focusType === 'lab' ? collab?.focus?.key : undefined}
            />
          </div>

          {collab && collab.insights.length > 0 && (
            <section className="rounded-xl border border-blue-200 bg-blue-50/60 p-4">
              <div className="mb-3 flex items-center gap-2">
                <Sparkles size={14} className="text-primary" />
                <h2 className="text-sm font-semibold text-slate-900">Agent collaboration insights</h2>
                {collab.isAgentRunning && (
                  <Loader2 size={12} className="animate-spin text-slate-400" />
                )}
              </div>
              <div className="grid gap-3 md:grid-cols-2">
                {collab.insights.map((card) => (
                  <article
                    key={card.id || card.title}
                    className="rounded-lg border border-blue-200 bg-white px-3 py-2"
                  >
                    <p className="text-xs font-semibold text-slate-900">{card.title}</p>
                    <p className="mt-1 text-[11px] leading-5 text-slate-600">{card.body}</p>
                  </article>
                ))}
              </div>
            </section>
          )}

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
                          <td className="py-3 font-semibold text-slate-800">
                            {medication.name}
                            <VerificationBadge status={medication.verification_status} />
                          </td>
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
                {(collab?.checklist?.length
                  ? collab.checklist
                  : snapshot.doctor_checklist_items.map((item) => ({
                      ...item,
                      agentNote: item.agent_note ?? undefined,
                    }))
                ).map((item) => {
                  const checked = item.done;
                  return (
                    <button
                      key={item.id}
                      type="button"
                      className="group flex w-full cursor-pointer items-start gap-3 text-left"
                      onClick={() =>
                        collab
                          ? collab.toggleChecklistItem(item.id)
                          : toggleChecklistItem(item)
                      }
                    >
                      <div
                        aria-hidden
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
                      <div className="min-w-0 flex-1">
                        <span
                          className={`text-[12px] leading-5 transition-colors duration-200 ${
                            checked ? 'text-slate-500 line-through' : 'text-slate-300'
                          }`}
                        >
                          {item.text}
                        </span>
                        {item.agentNote ? (
                          <p className="mt-1 text-[10px] leading-4 text-blue-300/90">{item.agentNote}</p>
                        ) : null}
                      </div>
                    </button>
                  );
                })}
              </div>
            </section>
          </div>
        </main>
      </div>

        <aside className="mt-4 lg:sticky lg:top-[7.25rem] lg:mt-0 lg:w-[400px] lg:shrink-0 lg:self-start xl:w-[480px]">
          <div className="flex h-[560px] flex-col overflow-hidden rounded-xl border border-border bg-white shadow-[0_1px_3px_0_rgb(15_23_42_/_0.08)] lg:h-[calc(100vh-8.25rem)]">
            {collab?.aside ?? (
              <AIChatPanel
                patientId={patient.id}
                patientName={patient.name}
                primaryDoctor={patient.primary_doctor ?? 'Doctor'}
                onUploaded={onRefresh}
                suggestedPrompts={suggestedPrompts}
              />
            )}
          </div>
        </aside>
      </div>
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

function VerificationBadge({ status }: { status?: 'verified' | 'unverified' | null }) {
  if (status !== 'unverified') return null;
  return (
    <span className="mt-1 inline-flex rounded border border-amber-200 bg-amber-50 px-1 py-0.5 text-[9px] font-semibold text-amber-800">
      AI extracted — unverified
    </span>
  );
}

function EmptyMessage({ text }: { text: string }) {
  return (
    <div className="col-span-full rounded-md border border-dashed border-slate-200 bg-slate-50 px-4 py-3 text-center text-xs text-slate-500">
      {text}
    </div>
  );
}
