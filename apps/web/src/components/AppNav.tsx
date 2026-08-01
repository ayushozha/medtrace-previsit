import { Mic, ScanLine, Sparkles, Stethoscope } from 'lucide-react';
import { Link, NavLink, useLocation } from 'react-router-dom';
import { cn } from '@/lib/utils';

const LINKS = [
  { to: '/patients', label: 'Patients', icon: Stethoscope, end: true },
  { to: '/imaging', label: 'Imaging', icon: ScanLine, end: false },
  { to: '/session', label: 'Session', icon: Mic, end: false },
  { to: '/yc-medplum-hackathon-demo', label: 'YC Demo', icon: Sparkles, end: false },
] as const;

/**
 * Top-level product nav. These were three separate apps on three ports reached by
 * hyperlink; they are now routes in one app.
 */
export function AppNav() {
  const { pathname } = useLocation();
  const isLanding = pathname === '/';

  return (
    <nav className={cn('sticky top-0 z-40 border-b backdrop-blur-xl transition-colors', isLanding ? 'border-white/[0.08] bg-[#050912]/90 text-white' : 'border-border bg-surface/85 text-foreground')}>
      <div className="mx-auto flex h-14 w-full max-w-[1480px] items-center gap-1 px-2 sm:px-6 lg:px-8">
        <Link to="/" className="mr-1 flex items-center gap-2 text-sm font-semibold sm:mr-4" aria-label="Medtrace home">
          <span className={cn('flex h-7 w-7 items-center justify-center rounded-lg', isLanding ? 'bg-[#0052cc] text-white shadow-[0_0_22px_rgba(59,130,246,0.4)]' : 'bg-primary text-white')}>
            <Stethoscope size={15} />
          </span>
          <span className="hidden sm:inline">Medtrace<span className={cn('ml-1 text-[9px] font-bold uppercase tracking-wider', isLanding ? 'text-[#60a5fa]' : 'text-primary')}>AI</span></span>
        </Link>
        {LINKS.map(({ to, label, icon: Icon, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            aria-label={label}
            className={({ isActive }) =>
              cn(
                'inline-flex h-9 items-center gap-2 rounded-lg px-2 text-sm font-medium transition sm:px-3',
                isLanding
                  ? isActive
                    ? 'bg-white/10 text-white'
                    : 'text-[#8490a3] hover:bg-white/[0.06] hover:text-white'
                  : isActive
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
