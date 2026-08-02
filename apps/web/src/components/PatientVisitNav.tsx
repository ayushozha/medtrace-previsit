import { ArrowLeft, ArrowUpRight, Mic, ScanLine, Stethoscope } from 'lucide-react';
import { Link, NavLink } from 'react-router-dom';
import { cn } from '@/lib/utils';

type PatientMode = 'chart' | 'imaging' | 'session';

const MODES: Array<{
  id: PatientMode;
  label: string;
  short: string;
  icon: typeof Stethoscope;
  path: (patientId: string) => string;
}> = [
  {
    id: 'chart',
    label: 'Chart',
    short: 'Chart',
    icon: Stethoscope,
    path: (id) => `/patients/${id}`,
  },
  {
    id: 'imaging',
    label: 'Imaging',
    short: 'Imaging',
    icon: ScanLine,
    path: (id) => `/patients/${id}/imaging`,
  },
  {
    id: 'session',
    label: 'Visit',
    short: 'Visit',
    icon: Mic,
    path: (id) => `/patients/${id}/session`,
  },
];

function initials(name: string): string {
  return name
    .split(' ')
    .map((part) => part[0])
    .filter(Boolean)
    .slice(0, 2)
    .join('')
    .toUpperCase();
}

/**
 * Sticky patient context + mode switcher for chart / imaging / visit routes.
 * Makes it obvious which patient the doctor is working on.
 */
export function PatientModeSwitcher({
  patientId,
  patientName,
  active,
  tone = 'light',
}: {
  patientId: string;
  patientName: string;
  active: PatientMode;
  tone?: 'light' | 'dark';
}) {
  const dark = tone === 'dark';

  return (
    <div
      className={cn(
        'sticky top-14 z-30 border-b backdrop-blur-xl',
        dark
          ? 'border-slate-800/90 bg-[#070b12]/92 text-slate-100'
          : 'border-border bg-surface/90 text-foreground',
      )}
    >
      <div className="mx-auto flex h-12 w-full max-w-[1480px] items-center gap-3 px-2 sm:px-6 lg:px-8">
        <Link
          to="/patients"
          className={cn(
            'inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border transition',
            dark
              ? 'border-slate-700 bg-slate-900 text-slate-300 hover:border-slate-500 hover:text-white'
              : 'border-border bg-slate-50 text-slate-500 hover:bg-slate-100 hover:text-slate-900',
          )}
          aria-label="Back to patient directory"
          title="Back to patients"
        >
          <ArrowLeft size={14} />
        </Link>

        <div className="flex min-w-0 items-center gap-2.5">
          <span
            className={cn(
              'flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-[11px] font-semibold',
              dark ? 'bg-cyan-400/15 text-cyan-100' : 'bg-primary text-primary-foreground',
            )}
          >
            {initials(patientName || 'PT')}
          </span>
          <div className="min-w-0">
            <p className={cn('truncate text-sm font-semibold leading-5', dark ? 'text-white' : 'text-slate-900')}>
              {patientName || 'Patient'}
            </p>
            <p
              className={cn(
                'truncate text-[10px] font-semibold uppercase tracking-[0.14em]',
                dark ? 'text-slate-500' : 'text-slate-400',
              )}
            >
              Working on this patient
            </p>
          </div>
        </div>

        <div
          className={cn(
            'ml-auto flex h-9 items-center gap-0.5 rounded-xl p-0.5',
            dark ? 'border border-slate-700 bg-slate-950/70' : 'border border-slate-200 bg-slate-100/80',
          )}
          role="tablist"
          aria-label="Patient workspace"
        >
          {MODES.map(({ id, label, short, icon: Icon, path }) => {
            const isActive = active === id;
            return (
              <NavLink
                key={id}
                to={path(patientId)}
                end={id === 'chart'}
                role="tab"
                aria-selected={isActive}
                aria-label={label}
                className={cn(
                  'inline-flex h-8 items-center gap-1.5 rounded-[10px] px-2.5 text-xs font-semibold transition sm:px-3',
                  isActive
                    ? dark
                      ? 'bg-cyan-400/15 text-cyan-100 shadow-[inset_0_0_0_1px_rgba(34,211,238,0.28)]'
                      : 'bg-white text-primary shadow-sm'
                    : dark
                      ? 'text-slate-400 hover:bg-white/[0.04] hover:text-slate-200'
                      : 'text-slate-500 hover:bg-white/70 hover:text-slate-800',
                )}
              >
                <Icon size={13} />
                <span className="hidden sm:inline">{label}</span>
                <span className="sm:hidden">{short}</span>
              </NavLink>
            );
          })}
        </div>
      </div>
    </div>
  );
}

/**
 * Chart launchpad — primary way to open imaging or a visit for this patient.
 */
export function PatientVisitLaunchpad({ patientId }: { patientId: string }) {
  return (
    <section
      className="clinical-panel overflow-hidden"
      style={{ animation: 'modern-panel-enter 420ms cubic-bezier(0.22, 1, 0.36, 1) both' }}
    >
      <div className="relative border-b border-border bg-[linear-gradient(135deg,#f8fafc_0%,#eef4ff_48%,#f8fafc_100%)] px-4 py-3">
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0 opacity-[0.35]"
          style={{
            backgroundImage:
              'radial-gradient(circle at 12% 20%, rgba(0,82,204,0.12), transparent 42%), radial-gradient(circle at 88% 0%, rgba(14,165,233,0.1), transparent 36%)',
          }}
        />
        <div className="relative">
          <p className="clinical-section-title">Open for this patient</p>
          <p className="mt-1 max-w-2xl text-sm text-slate-600">
            Imaging studies and visit recordings stay attached to this chart — pick a workspace to continue.
          </p>
        </div>
      </div>

      <div className="grid gap-3 p-4 md:grid-cols-2">
        <LaunchCard
          to={`/patients/${patientId}/imaging`}
          icon={ScanLine}
          eyebrow="Radiology"
          title="Imaging workspace"
          detail="Upload DICOM, segment ROIs, and review AI draft reports for this patient."
          accent="cyan"
          delayMs={40}
        />
        <LaunchCard
          to={`/patients/${patientId}/session`}
          icon={Mic}
          eyebrow="Encounter"
          title="Visit session"
          detail="Record or upload the consultation, then draft the clinical note with AI assist."
          accent="blue"
          delayMs={120}
        />
      </div>
    </section>
  );
}

function LaunchCard({
  to,
  icon: Icon,
  eyebrow,
  title,
  detail,
  accent,
  delayMs,
}: {
  to: string;
  icon: typeof ScanLine;
  eyebrow: string;
  title: string;
  detail: string;
  accent: 'cyan' | 'blue';
  delayMs: number;
}) {
  const accentClasses =
    accent === 'cyan'
      ? {
          icon: 'bg-cyan-50 text-cyan-700 ring-cyan-100',
          hover: 'hover:border-cyan-300/80 hover:shadow-[0_10px_30px_-18px_rgba(8,145,178,0.55)]',
          arrow: 'group-hover:text-cyan-700',
        }
      : {
          icon: 'bg-blue-50 text-primary ring-blue-100',
          hover: 'hover:border-primary/35 hover:shadow-[0_10px_30px_-18px_rgba(0,82,204,0.45)]',
          arrow: 'group-hover:text-primary',
        };

  return (
    <Link
      to={to}
      className={cn(
        'group relative block overflow-hidden rounded-xl border border-slate-200 bg-white p-4 transition duration-300',
        accentClasses.hover,
      )}
      style={{
        animation: `modern-panel-enter 480ms cubic-bezier(0.22, 1, 0.36, 1) ${delayMs}ms both`,
      }}
    >
      <div className="flex items-start gap-3">
        <span
          className={cn(
            'flex h-10 w-10 shrink-0 items-center justify-center rounded-xl ring-1 transition group-hover:scale-[1.03]',
            accentClasses.icon,
          )}
        >
          <Icon size={18} />
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-[10px] font-bold uppercase tracking-[0.16em] text-slate-400">{eyebrow}</p>
          <div className="mt-1 flex items-center gap-2">
            <h3 className="text-base font-semibold text-slate-900">{title}</h3>
            <ArrowUpRight
              size={15}
              className={cn('text-slate-300 transition group-hover:translate-x-0.5 group-hover:-translate-y-0.5', accentClasses.arrow)}
            />
          </div>
          <p className="mt-1.5 text-sm leading-5 text-slate-600">{detail}</p>
        </div>
      </div>
    </Link>
  );
}
