import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useAgent, UseAgentUpdate } from '@copilotkit/react-core/v2';
import { Loader2, Send, Sparkles } from 'lucide-react';

import { AIChatPanel } from '../AIChatPanel';
import { buildPatientContext } from './buildPatientContext';
import { DashboardCollabProvider } from './DashboardCollabContext';
import {
  DASHBOARD_AGENT_ID,
  checklistFromSnapshot,
  type AgentInsightCard,
  type ChecklistItem,
  type ClinicalFocus,
  type DashboardAgentState,
} from './collabTypes';
import type { ClinicalSnapshot } from '@/lib/types';

type AsideTab = 'classic' | 'agent';

function asChecklist(value: unknown): ChecklistItem[] {
  if (!Array.isArray(value)) return [];
  const out: ChecklistItem[] = [];
  value.forEach((item, index) => {
    if (!item || typeof item !== 'object') return;
    const row = item as Record<string, unknown>;
    const text = String(row.text ?? '');
    if (!text) return;
    out.push({
      id: String(row.id ?? `chk-${index}`),
      text,
      done: Boolean(row.done),
      ...(row.agentNote != null ? { agentNote: String(row.agentNote) } : {}),
    });
  });
  return out;
}

function asInsights(value: unknown): AgentInsightCard[] {
  if (!Array.isArray(value)) return [];
  const out: AgentInsightCard[] = [];
  value.forEach((item, index) => {
    if (!item || typeof item !== 'object') return;
    const row = item as Record<string, unknown>;
    const title = String(row.title ?? '');
    const body = String(row.body ?? '');
    if (!title && !body) return;
    out.push({
      id: String(row.id ?? `insight-${index}`),
      title: title || 'Insight',
      body,
      ...(row.relatedChecklistId != null
        ? { relatedChecklistId: String(row.relatedChecklistId) }
        : {}),
    });
  });
  return out;
}

function asFocus(value: unknown): ClinicalFocus | null {
  if (!value || typeof value !== 'object') return null;
  const row = value as Record<string, unknown>;
  const type = String(row.type ?? '');
  const key = String(row.key ?? '');
  if (!type || !key) return null;
  if (type !== 'lab' && type !== 'condition' && type !== 'med' && type !== 'alert') return null;
  return { type, key };
}

/** CopilotKit often keeps tool args on messages even when custom LangGraph state keys don't sync. */
function extractUiFromMessages(messages: unknown[]): {
  insights: AgentInsightCard[];
  focus: ClinicalFocus | null;
  notes: Record<string, string>;
} {
  let insights: AgentInsightCard[] = [];
  let focus: ClinicalFocus | null = null;
  let notes: Record<string, string> = {};

  for (const raw of messages) {
    if (!raw || typeof raw !== 'object') continue;
    const msg = raw as {
      role?: string;
      toolCalls?: Array<{ function?: { name?: string; arguments?: string }; name?: string; args?: unknown }>;
    };
    if (msg.role !== 'assistant' || !Array.isArray(msg.toolCalls)) continue;
    for (const tc of msg.toolCalls) {
      const name = tc.function?.name || tc.name;
      if (name !== 'update_dashboard_ui') continue;
      let args: Record<string, unknown> = {};
      if (typeof tc.function?.arguments === 'string') {
        try {
          args = JSON.parse(tc.function.arguments) as Record<string, unknown>;
        } catch {
          args = {};
        }
      } else if (tc.args && typeof tc.args === 'object') {
        args = tc.args as Record<string, unknown>;
      }
      const parsedInsights = asInsights(
        typeof args.insights_json === 'string'
          ? (() => {
              try {
                return JSON.parse(args.insights_json);
              } catch {
                return [];
              }
            })()
          : args.insights_json,
      );
      if (parsedInsights.length) insights = parsedInsights;
      const focusType = String(args.focus_type ?? '').trim();
      const focusKey = String(args.focus_key ?? '').trim();
      if (focusType && focusKey) {
        focus = asFocus({ type: focusType, key: focusKey });
      }
      const rawNotes = args.checklist_notes_json;
      try {
        const parsedNotes =
          typeof rawNotes === 'string' ? JSON.parse(rawNotes) : rawNotes;
        if (parsedNotes && typeof parsedNotes === 'object') {
          notes = Object.fromEntries(
            Object.entries(parsedNotes as Record<string, unknown>).map(([k, v]) => [
              k,
              String(v),
            ]),
          );
        }
      } catch {
        /* ignore */
      }
    }
  }
  return { insights, focus, notes };
}

/**
 * CopilotKit collaboration layer for the patient chart.
 * Must render under <CopilotKit agent="dashboard_clinical">.
 *
 * Local React state is the UI source of truth; CopilotKit agent.state is synced into it
 * after runs so the chart updates even when AG-UI state delivery is delayed.
 */
export function DashboardCollabLayer({
  snapshot,
  onRefresh,
  children,
}: {
  snapshot: ClinicalSnapshot;
  onRefresh: () => void;
  children: React.ReactNode;
}) {
  const [asideTab, setAsideTab] = useState<AsideTab>('agent');
  const [commandText, setCommandText] = useState('');
  const [errorMsg, setErrorMsg] = useState('');
  const [checklist, setChecklist] = useState<ChecklistItem[]>(() =>
    checklistFromSnapshot(snapshot.doctor_checklist),
  );
  const [insights, setInsights] = useState<AgentInsightCard[]>([]);
  const [focus, setFocus] = useState<ClinicalFocus | null>(null);
  const seededRef = useRef(false);
  const prevRunning = useRef(false);

  const { agent } = useAgent({
    agentId: DASHBOARD_AGENT_ID,
    updates: [
      UseAgentUpdate.OnMessagesChanged,
      UseAgentUpdate.OnStateChanged,
      UseAgentUpdate.OnRunStatusChanged,
    ],
  });

  const pushStateToAgent = useCallback(
    (next: {
      checklist?: ChecklistItem[];
      insights?: AgentInsightCard[];
      focus?: ClinicalFocus | null;
    }) => {
      const prev = (agent.state as DashboardAgentState | undefined) ?? {};
      agent.setState({
        ...prev,
        patient_id: snapshot.patient.id,
        patient_context: buildPatientContext(snapshot),
        checklist: next.checklist ?? checklist,
        insights: next.insights ?? insights,
        focus: next.focus !== undefined ? next.focus : focus,
      } satisfies DashboardAgentState);
    },
    [agent, checklist, focus, insights, snapshot],
  );

  // Seed agent + local UI once.
  useEffect(() => {
    if (seededRef.current) return;
    const initial = checklistFromSnapshot(snapshot.doctor_checklist);
    setChecklist(initial);
    setInsights([]);
    setFocus(null);
    agent.setState({
      patient_id: snapshot.patient.id,
      patient_context: buildPatientContext(snapshot),
      checklist: initial,
      insights: [],
      focus: null,
    } satisfies DashboardAgentState);
    seededRef.current = true;
  }, [agent, snapshot]);

  // Keep checklist text in sync if snapshot checklist changes; preserve done/notes.
  const checklistKey = snapshot.doctor_checklist.join('\0');
  useEffect(() => {
    if (!seededRef.current) return;
    setChecklist((prev) => {
      const byText = new Map(prev.map((item) => [item.text, item]));
      return snapshot.doctor_checklist.map((text, index) => {
        const existing = byText.get(text);
        return {
          id: existing?.id ?? `chk-${index}`,
          text,
          done: existing?.done ?? false,
          agentNote: existing?.agentNote,
        };
      });
    });
  }, [snapshot.patient.id, checklistKey, snapshot.doctor_checklist]);

  // When an agent run finishes, pull shared state into local UI (chart widgets).
  useEffect(() => {
    const running = agent.isRunning;
    const finished = prevRunning.current && !running;
    prevRunning.current = running;
    if (!finished && !running) {
      // Also apply mid-run predict_state / state patches when present.
    }
    const state = (agent.state as DashboardAgentState | undefined) ?? {};
    const nextChecklist = asChecklist(state.checklist);
    const nextInsights = asInsights(state.insights);
    const nextFocus = asFocus(state.focus);

    if (nextChecklist.length) {
      setChecklist((prev) => {
        // Prefer agent notes/done when ids match; fall back to previous done flags by text.
        const prevById = new Map(prev.map((item) => [item.id, item]));
        const prevByText = new Map(prev.map((item) => [item.text, item]));
        return nextChecklist.map((item) => {
          const fromPrev = prevById.get(item.id) ?? prevByText.get(item.text);
          return {
            ...item,
            done: item.done || Boolean(fromPrev?.done),
            agentNote: item.agentNote ?? fromPrev?.agentNote,
          };
        });
      });
    }
    // Only apply non-empty patches from shared state — never wipe local UI on empty sync.
    if (nextInsights.length) setInsights(nextInsights);
    if (nextFocus) setFocus(nextFocus);

    if (finished) {
      const fromMessages = extractUiFromMessages(agent.messages as unknown[]);
      if (fromMessages.insights.length) setInsights(fromMessages.insights);
      if (fromMessages.focus) setFocus(fromMessages.focus);
      if (Object.keys(fromMessages.notes).length) {
        setChecklist((prev) =>
          prev.map((item) => ({
            ...item,
            agentNote: fromMessages.notes[item.id] ?? item.agentNote,
          })),
        );
      }
    }
  }, [agent.isRunning, agent.state, agent.messages]);

  const runLockRef = useRef(false);

  const askAgent = useCallback(
    async (text: string, nextChecklist?: ChecklistItem[]) => {
      const trimmed = text.trim();
      if (!trimmed) return;
      if (runLockRef.current || agent.isRunning) {
        setErrorMsg('Agent is still working — wait a moment, then try again.');
        return;
      }
      setErrorMsg('');
      const checklistForRun = nextChecklist ?? checklist;
      runLockRef.current = true;
      try {
        agent.setState({
          patient_id: snapshot.patient.id,
          patient_context: buildPatientContext(snapshot),
          checklist: checklistForRun,
          insights,
          focus,
        } satisfies DashboardAgentState);
        agent.addMessage({
          id: Math.random().toString(36).slice(2),
          role: 'user',
          content: trimmed,
        });
        await agent.runAgent();
        // Prefer shared state when present; fall back to tool-call args on messages
        // (CopilotKit + LangGraphHttpAgent often omit custom state keys).
        const state = (agent.state as DashboardAgentState | undefined) ?? {};
        const fromMessages = extractUiFromMessages(agent.messages as unknown[]);
        const syncedChecklist = asChecklist(state.checklist);
        const syncedInsights =
          asInsights(state.insights).length > 0
            ? asInsights(state.insights)
            : fromMessages.insights;
        const syncedFocus = asFocus(state.focus) ?? fromMessages.focus;
        if (syncedChecklist.length) {
          setChecklist(
            syncedChecklist.map((item) => ({
              ...item,
              agentNote: fromMessages.notes[item.id] ?? item.agentNote,
            })),
          );
        } else if (Object.keys(fromMessages.notes).length) {
          setChecklist((prev) =>
            prev.map((item) => ({
              ...item,
              agentNote: fromMessages.notes[item.id] ?? item.agentNote,
            })),
          );
        }
        if (syncedInsights.length) setInsights(syncedInsights);
        if (syncedFocus) setFocus(syncedFocus);
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Dashboard agent run failed';
        console.error('Dashboard agent run failed:', err);
        setErrorMsg(message);
      } finally {
        runLockRef.current = false;
      }
    },
    [agent, checklist, focus, insights, snapshot],
  );

  const toggleChecklistItem = useCallback(
    (id: string) => {
      setChecklist((prev) => {
        const current = prev.map((item) =>
          item.id === id ? { ...item, done: !item.done } : item,
        );
        const toggled = current.find((item) => item.id === id);
        pushStateToAgent({ checklist: current });
        if (toggled?.done) {
          // Defer so React state settles and we don't double-fire under StrictMode.
          window.setTimeout(() => {
            void askAgent(
              `The doctor marked checklist item "${toggled.text}" (id=${toggled.id}) as DONE. ` +
                'Analyze against the patient context, call update_dashboard_ui with 1-2 concise insights ' +
                'and optionally highlight a related lab/condition/med/alert, and add a short agentNote on that item.',
              current,
            );
          }, 50);
        }
        return current;
      });
    },
    [askAgent, pushStateToAgent],
  );

  const assistantMessages = agent.messages.filter((m) => m.role === 'assistant' && m.content);
  const latestAssistant = assistantMessages[assistantMessages.length - 1];

  const handleSend = async () => {
    if (!commandText.trim()) return;
    const text = commandText;
    setCommandText('');
    await askAgent(text);
  };

  const renderInsightCards = (cards: AgentInsightCard[]) =>
    cards.slice(0, 3).map((card) => (
      <article
        key={card.id || card.title}
        className="rounded-lg border border-blue-200 bg-blue-50/70 px-3 py-2"
      >
        <p className="text-xs font-semibold text-blue-950">{card.title}</p>
        <p className="mt-1 text-[11px] leading-5 text-blue-900/80">{card.body}</p>
      </article>
    ));

  const aside = (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 border-b border-border bg-slate-50 p-1">
        <button
          type="button"
          onClick={() => setAsideTab('agent')}
          className={`flex-1 rounded-md px-2 py-1.5 text-[11px] font-semibold ${
            asideTab === 'agent'
              ? 'bg-white text-slate-900 shadow-sm'
              : 'text-slate-500 hover:text-slate-800'
          }`}
        >
          Agent collab
        </button>
        <button
          type="button"
          onClick={() => setAsideTab('classic')}
          className={`flex-1 rounded-md px-2 py-1.5 text-[11px] font-semibold ${
            asideTab === 'classic'
              ? 'bg-white text-slate-900 shadow-sm'
              : 'text-slate-500 hover:text-slate-800'
          }`}
        >
          Classic chat
        </button>
      </div>

      {asideTab === 'classic' ? (
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
          <p className="shrink-0 border-b border-amber-100 bg-amber-50 px-3 py-2 text-[10px] leading-4 text-amber-900">
            Classic chat answers with text only (Zep memory). It does not move checklist items,
            highlights, or insight cards — use <strong>Agent collab</strong> for that.
          </p>
          <div className="min-h-0 flex-1 overflow-hidden">
            <AIChatPanel
              patientId={snapshot.patient.id}
              patientName={snapshot.patient.name}
              primaryDoctor={snapshot.patient.primary_doctor ?? 'Doctor'}
              onUploaded={onRefresh}
            />
          </div>
        </div>
      ) : (
        <div className="flex min-h-0 flex-1 flex-col">
          <div className="border-b border-border px-3 py-2">
            <div className="flex items-center gap-2 text-xs font-semibold text-slate-800">
              <Sparkles size={14} className="text-primary" />
              Checklist co-pilot
              {agent.isRunning && <Loader2 size={12} className="animate-spin text-slate-400" />}
            </div>
            <p className="mt-1 text-[10px] leading-4 text-slate-500">
              Mark checklist items done — the agent analyzes and updates insights on the chart.
              Non-diagnostic CDS aid.
            </p>
          </div>

          <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-3 py-3">
            {errorMsg ? (
              <p className="rounded-md border border-red-200 bg-red-50 px-2 py-1.5 text-[11px] text-red-700">
                {errorMsg}
              </p>
            ) : null}
            {insights.length > 0 ? (
              renderInsightCards(insights)
            ) : (
              <p className="text-[11px] leading-5 text-slate-500">
                No agent insights yet. Check a review-checklist item to trigger analysis.
              </p>
            )}
            {latestAssistant?.content ? (
              <div className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-[11px] leading-5 text-slate-700">
                {String(latestAssistant.content)}
              </div>
            ) : null}
          </div>

          <div className="shrink-0 border-t border-border p-2">
            <div className="flex items-end gap-2">
              <textarea
                value={commandText}
                onChange={(e) => setCommandText(e.target.value)}
                rows={2}
                placeholder="Ask the co-pilot (e.g. what’s left?)"
                className="min-h-[40px] flex-1 resize-none rounded-md border border-slate-200 px-2 py-1.5 text-xs outline-none focus:border-primary"
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    void handleSend();
                  }
                }}
              />
              <button
                type="button"
                onClick={() => void handleSend()}
                disabled={agent.isRunning || !commandText.trim()}
                className="inline-flex h-9 w-9 items-center justify-center rounded-md bg-primary text-primary-foreground disabled:opacity-40"
                title="Send"
              >
                <Send size={14} />
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );

  const collabApi = useMemo(
    () => ({
      checklist,
      insights,
      focus,
      isAgentRunning: agent.isRunning,
      toggleChecklistItem,
      askAgent: (text: string) => askAgent(text),
      aside,
    }),
    // aside rebuilt when these change
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [
      agent.isRunning,
      askAgent,
      checklist,
      focus,
      insights,
      toggleChecklistItem,
      asideTab,
      commandText,
      errorMsg,
      latestAssistant?.content,
    ],
  );

  return <DashboardCollabProvider value={collabApi}>{children}</DashboardCollabProvider>;
}
