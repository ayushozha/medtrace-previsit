import { RefreshCcw } from 'lucide-react';
import type { TimelinePeriod } from '@/lib/types';

interface TimelineProps {
  timeline: TimelinePeriod[];
  onRefresh?: () => void;
}

export function Timeline({ timeline, onRefresh }: TimelineProps) {
  const ordered = [...timeline].reverse();
  return (
    <section className="clinical-panel flex h-full min-h-[360px] flex-col overflow-hidden">
      <div className="flex flex-row items-center justify-between border-b border-border bg-slate-50 p-4">
        <h2 className="clinical-section-title">Timeline Analysis</h2>
        <button
          type="button"
          onClick={onRefresh}
          className="rounded-md p-1.5 text-slate-400 transition-colors hover:bg-slate-100 hover:text-primary"
          title="Sync Temporal Graph"
        >
          <RefreshCcw size={12} />
        </button>
      </div>
      <div className="flex-1 overflow-y-auto p-4">
        {ordered.length === 0 ? (
          <p className="rounded-md border border-dashed border-slate-200 bg-slate-50 px-4 py-8 text-center text-xs text-slate-500">
            No timeline yet. Upload a document to populate Zep episodes.
          </p>
        ) : (
          <div className="relative space-y-5">
            <div className="absolute left-[7px] top-2 bottom-2 w-px bg-slate-200" />
            {ordered.map((period, i) => (
              <div key={`${period.date}-${i}`} className="relative pl-6">
                <div
                  className={`absolute left-0 top-1.5 h-3.5 w-3.5 rounded-full border-2 border-white ${
                    i === 0 ? 'bg-primary' : 'bg-slate-300'
                  }`}
                />
                <p
                  className={`text-[11px] font-semibold uppercase tracking-[0.08em] ${
                    i === 0 ? 'text-primary' : 'text-slate-400'
                  }`}
                >
                  {period.date}
                </p>
                <div className="mt-1 space-y-1.5">
                  {period.events.map((event, j) => (
                    <div
                      key={`${event}-${j}`}
                      className={`text-xs leading-5 ${
                        i === 0 ? 'font-semibold' : 'font-medium'
                      } text-slate-800`}
                    >
                      {event}
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}
