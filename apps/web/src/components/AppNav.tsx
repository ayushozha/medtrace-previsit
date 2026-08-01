import { Mic, ScanLine, Stethoscope } from 'lucide-react';
import { NavLink } from 'react-router-dom';
import { cn } from '@/lib/utils';

const LINKS = [
  { to: '/', label: 'Patients', icon: Stethoscope, end: true },
  { to: '/imaging', label: 'Imaging', icon: ScanLine, end: false },
  { to: '/session', label: 'Session', icon: Mic, end: false },
] as const;

/**
 * Top-level product nav. These were three separate apps on three ports reached by
 * hyperlink; they are now routes in one app.
 */
export function AppNav() {
  return (
    <nav className="sticky top-0 z-40 border-b border-border bg-surface/85 backdrop-blur">
      <div className="mx-auto flex h-14 w-full max-w-[1440px] items-center gap-1 px-2 sm:px-6 lg:px-8">
        <span className="mr-1 flex items-center gap-2 text-sm font-semibold text-foreground sm:mr-4">
          <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-primary text-white">
            <Stethoscope size={15} />
          </span>
          <span className="hidden sm:inline">Medtrace</span>
        </span>
        {LINKS.map(({ to, label, icon: Icon, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            aria-label={label}
            className={({ isActive }) =>
              cn(
                'inline-flex h-9 items-center gap-2 rounded-lg px-2 text-sm font-medium transition sm:px-3',
                isActive
                  ? 'bg-primary/10 text-primary'
                  : 'text-slate-600 hover:bg-slate-100 hover:text-slate-900',
              )
            }
          >
            <Icon size={15} />
            <span className="hidden sm:inline">{label}</span>
          </NavLink>
        ))}
      </div>
    </nav>
  );
}
