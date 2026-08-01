import { useState } from 'react';
import {
  ArrowRight,
  CalendarDays,
  FileText,
  Loader2,
  Plus,
  Stethoscope,
  TriangleAlert,
  UserRound,
} from 'lucide-react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { usePatients } from '@/hooks/usePatients';
import type { Patient, RiskLevel } from '@/lib/types';

interface MainDashboardProps {
  onSelectPatient: (patientId: string) => void;
}

const RISK_BADGE: Record<RiskLevel, string> = {
  High: 'border-red-200 bg-red-50 text-red-700',
  Medium: 'border-yellow-200 bg-yellow-50 text-yellow-800',
  Low: 'border-emerald-200 bg-emerald-50 text-emerald-700',
};

export function MainDashboard({ onSelectPatient }: MainDashboardProps) {
  const { patients, loading, error, refresh, createPatient } = usePatients();
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [displayName, setDisplayName] = useState('');

  const openDialog = () => {
    setCreateError(null);
    setDialogOpen(true);
  };

  const handleCreatePatient = async () => {
    const display = displayName.trim();
    if (!display || creating) return;
    const slug = display
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/(^-|-$)/g, '')
      .slice(0, 32);
    const zepUserId = `react-${slug}-${Date.now().toString(36)}`;
    setCreating(true);
    setCreateError(null);
    try {
      const created = await createPatient({ zep_user_id: zepUserId, display_name: display });
      setDialogOpen(false);
      setDisplayName('');
      onSelectPatient(created.id);
    } catch (err) {
      setCreateError((err as Error).message);
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className="mx-auto min-h-screen w-full max-w-[1160px] px-4 py-6 sm:px-6 lg:px-8">
      <header className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-primary text-white shadow-[0_8px_18px_-12px_rgb(0_82_204)]">
            <Stethoscope size={20} />
          </div>
          <div>
            <h1 className="text-2xl font-semibold leading-8 text-slate-950">Medtrace</h1>
            <p className="text-sm leading-5 text-slate-600">Patient directory</p>
          </div>
        </div>

        <button
          type="button"
          disabled={creating}
          onClick={openDialog}
          className="inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-primary px-4 text-sm font-semibold text-white shadow-sm transition hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500/20 disabled:opacity-60"
        >
          {creating ? <Loader2 size={16} className="animate-spin" /> : <Plus size={16} />}
          Add new patient
        </button>
      </header>

      {createError && !dialogOpen && (
        <div className="mb-4 flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
          <TriangleAlert size={14} className="mt-0.5 shrink-0" />
          <div>
            <p className="font-semibold">Could not create patient</p>
            <p>{createError}</p>
          </div>
        </div>
      )}

      <section className="rounded-xl border border-border bg-white shadow-[0_1px_3px_0_rgb(15_23_42_/_0.08)]">
        <div className="border-b border-border px-6 py-5">
          <p className="clinical-section-title">Patients</p>
          <h2 className="mt-1 text-xl font-semibold leading-7 text-slate-800">Select a patient</h2>
        </div>

        {loading && patients.length === 0 ? (
          <div className="grid place-items-center gap-2 px-6 py-16 text-sm text-slate-500">
            <Loader2 size={20} className="animate-spin" />
            <span>Loading patients...</span>
          </div>
        ) : error ? (
          <div className="m-6 flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
            <TriangleAlert size={16} className="mt-0.5 shrink-0" />
            <div className="space-y-1">
              <p className="font-semibold">Could not load patients</p>
              <p className="text-xs">{error}</p>
              <button
                type="button"
                onClick={() => void refresh()}
                className="mt-2 inline-flex items-center gap-2 rounded-md border border-red-200 bg-white px-3 py-1.5 text-xs font-semibold text-red-700 hover:bg-red-100"
              >
                Retry
              </button>
            </div>
          </div>
        ) : patients.length === 0 ? (
          <div className="grid place-items-center gap-2 px-6 py-16 text-center text-sm text-slate-500">
            <p className="font-semibold text-slate-700">No patients yet</p>
            <p className="max-w-xs text-xs">
              Click <span className="font-semibold">Add new patient</span> to create the first chart.
            </p>
          </div>
        ) : (
          <div className="grid gap-4 p-6 sm:grid-cols-2 lg:grid-cols-3">
            {patients.map((patient) => (
              <PatientCard
                key={patient.id}
                patient={patient}
                onOpen={() => onSelectPatient(patient.id)}
              />
            ))}
          </div>
        )}
      </section>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Add new patient</DialogTitle>
            <DialogDescription>
              Creates a chart and a Zep memory user for the patient.
            </DialogDescription>
          </DialogHeader>

          <form
            onSubmit={(e) => {
              e.preventDefault();
              void handleCreatePatient();
            }}
            className="grid gap-3"
          >
            <div className="grid gap-1.5">
              <label htmlFor="new-patient-name" className="text-xs font-semibold text-slate-600">
                Display name
              </label>
              <Input
                id="new-patient-name"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                placeholder="e.g. Jane Doe"
                autoFocus
                disabled={creating}
              />
            </div>

            {createError && (
              <div className="flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
                <TriangleAlert size={14} className="mt-0.5 shrink-0" />
                <div>
                  <p className="font-semibold">Could not create patient</p>
                  <p>{createError}</p>
                </div>
              </div>
            )}

            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => setDialogOpen(false)}
                disabled={creating}
              >
                Cancel
              </Button>
              <Button type="submit" disabled={!displayName.trim() || creating}>
                {creating ? <Loader2 size={14} className="animate-spin" /> : null}
                Create patient
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function PatientCard({ patient, onOpen }: { patient: Patient; onOpen: () => void }) {
  const sexLabel = patient.sex === 'M' ? 'Male' : patient.sex === 'F' ? 'Female' : 'Other';
  const ageLabel = patient.age ? `${patient.age} yrs` : 'Age not set';
  return (
    <button
      type="button"
      onClick={onOpen}
      className="group flex min-h-[220px] flex-col rounded-xl border border-border bg-white p-4 text-left shadow-sm transition-all hover:-translate-y-0.5 hover:border-blue-200 hover:shadow-[0_18px_45px_-34px_rgba(15,23,42,0.55)] focus:outline-none focus:ring-2 focus:ring-blue-500/20"
    >
      <div className="mb-5 flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3">
          <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border border-blue-100 bg-blue-50 text-sm font-semibold text-primary">
            {getInitials(patient.name)}
          </div>
          <div className="min-w-0">
            <h3 className="truncate text-base font-semibold leading-6 text-slate-950">{patient.name}</h3>
            <p className="font-mono text-[11px] font-medium leading-5 text-slate-500">
              {patient.zep_user_id}
            </p>
          </div>
        </div>
        <span className={`rounded-md border px-2 py-1 text-[10px] font-semibold ${RISK_BADGE[patient.risk]}`}>
          {patient.risk}
        </span>
      </div>

      <div className="grid gap-3">
        <PatientDetail icon={<UserRound size={14} />} label="Age / sex" value={`${ageLabel} - ${sexLabel}`} />
        <PatientDetail
          icon={<CalendarDays size={14} />}
          label="Last visit"
          value={patient.last_visit ?? 'No visits'}
        />
        <PatientDetail
          icon={<FileText size={14} />}
          label="Documents"
          value={`${patient.document_count} uploaded`}
        />
      </div>

      <div className="mt-auto pt-5">
        <div className="flex items-center justify-between rounded-lg border border-border bg-slate-50 px-3 py-2.5 transition group-hover:border-blue-100 group-hover:bg-blue-50">
          <span className="text-sm font-semibold text-primary">Open patient</span>
          <span className="flex h-7 w-7 items-center justify-center rounded-md bg-white text-slate-400 shadow-sm transition group-hover:text-primary">
            <ArrowRight size={14} />
          </span>
        </div>
      </div>
    </button>
  );
}

function PatientDetail({ icon, label, value }: { icon: React.ReactNode; label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-3 text-sm leading-5">
      <span className="inline-flex items-center gap-2 text-slate-500">
        <span className="text-slate-400">{icon}</span>
        {label}
      </span>
      <span className="font-medium text-slate-900">{value}</span>
    </div>
  );
}

function getInitials(name: string) {
  return name
    .split(' ')
    .map((part) => part[0])
    .filter(Boolean)
    .slice(0, 2)
    .join('')
    .toUpperCase();
}
