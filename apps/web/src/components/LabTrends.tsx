import { Minus, TrendingDown, TrendingUp } from 'lucide-react';
import { Area, AreaChart, ReferenceLine, ResponsiveContainer, YAxis } from 'recharts';
import type { LabTrend } from '@/lib/types';

interface LabTrendsProps {
  labs: LabTrend[];
}

/** First numeric value embedded in a lab string like "142 mg/dL" or "7.2%". */
function parseLabValue(raw: string | null): number | null {
  if (!raw) return null;
  const match = raw.replace(/,/g, '').match(/-?\d+(?:\.\d+)?/);
  return match ? Number(match[0]) : null;
}

/** Reference range like "70-99" → [lo, hi], when parseable. */
function parseRange(range: string | null): [number, number] | null {
  const match = range?.match(/(-?\d+(?:\.\d+)?)\s*[-–]\s*(-?\d+(?:\.\d+)?)/);
  return match ? [Number(match[1]), Number(match[2])] : null;
}

/**
 * Two-point sparkline (previous → latest) for one analyte. LabTrend only carries
 * latest/previous pairs, so a per-row mini chart fits the data shape better than a
 * full multi-series trend chart. Stroke color mirrors the trend semantics and the
 * --chart-* tokens in index.css.
 */
function LabSparkline({ lab }: { lab: LabTrend }) {
  const latest = parseLabValue(lab.latest);
  const previous = parseLabValue(lab.previous);
  if (latest === null || previous === null) {
    return <span className="text-[10px] text-slate-300">-</span>;
  }

  const color =
    lab.trend === 'Worsening' ? '#ef4444' : lab.trend === 'Improving' ? '#22c55e' : '#0052cc';
  const data = [
    { i: 0, v: previous },
    { i: 1, v: latest },
  ];
  const range = parseRange(lab.range);
  const values = [previous, latest, ...(range ?? [])];
  const min = Math.min(...values);
  const max = Math.max(...values);
  const pad = (max - min) * 0.15 || 1;

  return (
    <div className="h-8 w-20" aria-hidden>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 2, right: 2, bottom: 2, left: 2 }}>
          <YAxis hide domain={[min - pad, max + pad]} />
          {range?.map((bound) => (
            <ReferenceLine key={bound} y={bound} stroke="#cbd5e1" strokeDasharray="2 2" />
          ))}
          <Area
            type="monotone"
            dataKey="v"
            stroke={color}
            strokeWidth={1.5}
            fill={color}
            fillOpacity={0.12}
            dot={{ r: 1.5, fill: color, strokeWidth: 0 }}
            isAnimationActive
            animationDuration={700}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

export function LabTrends({ labs }: LabTrendsProps) {
  return (
    <section className="clinical-panel flex h-full min-h-[360px] flex-col overflow-hidden">
      <div className="border-b border-border bg-slate-50 p-4">
        <div className="flex items-center justify-between gap-3">
          <h2 className="clinical-section-title">Temporal Lab Trends</h2>
          <span className="clinical-chip border-blue-200 bg-blue-50 text-primary">Evidence linked</span>
        </div>
      </div>
      <div className="flex-1 overflow-x-auto p-4">
        {labs.length === 0 ? (
          <p className="rounded-md border border-dashed border-slate-200 bg-slate-50 px-4 py-8 text-center text-xs text-slate-500">
            No lab values detected in Zep memory yet.
          </p>
        ) : (
          <>
            <table className="w-full text-left">
              <thead className="border-b border-slate-100 text-[11px] uppercase tracking-[0.12em] text-slate-500">
                <tr>
                  <th className="pb-2 font-semibold">Metric</th>
                  <th className="pb-2 font-semibold">Value</th>
                  <th className="hidden pb-2 font-semibold sm:table-cell">Range</th>
                  <th className="pb-2 font-semibold">Trace</th>
                  <th className="pb-2 font-semibold">Trend</th>
                </tr>
              </thead>
              <tbody className="text-xs">
                {labs.map((lab) => (
                  <tr
                    key={lab.test}
                    className="border-b border-slate-50 transition-colors last:border-0 hover:bg-slate-50/80"
                  >
                    <td className="py-3">
                      <p className="font-semibold text-slate-800">{lab.test}</p>
                      <p className="mt-0.5 text-[10px] text-slate-400">{lab.date ?? ''}</p>
                    </td>
                    <td className="py-3">
                      <span
                        className={`${
                          lab.status === 'High' ? 'text-red-600 font-bold' : 'text-slate-700'
                        }`}
                      >
                        {lab.latest}
                      </span>
                      {lab.previous && (
                        <p className="mt-0.5 font-mono text-[10px] text-slate-400">Prev {lab.previous}</p>
                      )}
                    </td>
                    <td className="hidden py-3 font-mono text-[11px] text-slate-500 sm:table-cell">
                      {lab.range ?? '-'}
                    </td>
                    <td className="py-3 pr-3">
                      <LabSparkline lab={lab} />
                    </td>
                    <td
                      className={`py-3 flex items-center gap-1.5 ${
                        lab.trend === 'Worsening'
                          ? 'text-red-500'
                          : lab.trend === 'Improving'
                            ? 'text-emerald-500'
                            : 'text-slate-500'
                      }`}
                    >
                      {lab.trend === 'Worsening' ? (
                        <TrendingUp size={14} className="text-red-400" />
                      ) : lab.trend === 'Improving' ? (
                        <TrendingDown size={14} className="text-emerald-400" />
                      ) : (
                        <Minus size={14} />
                      )}
                      <span className="font-medium">{lab.trend}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {labs[0] && (
              <div className="mt-4 rounded-lg border border-blue-100 bg-blue-50 px-3 py-2">
                <p className="text-[11px] font-medium leading-5 text-blue-900">
                  Latest {labs[0].test} = {labs[0].latest}
                  {labs[0].previous ? `, previously ${labs[0].previous}` : ''} ({labs[0].trend.toLowerCase()}
                  ).
                </p>
              </div>
            )}
          </>
        )}
      </div>
    </section>
  );
}
