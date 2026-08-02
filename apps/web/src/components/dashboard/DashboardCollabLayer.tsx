import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  CopilotChat,
  useAgent,
  UseAgentUpdate,
} from '@copilotkit/react-core/v2';

import { buildPatientContext } from './buildPatientContext';
import { DashboardCollabProvider } from './DashboardCollabContext';
import {
  CHART_ROUTER_AGENT_ID,
  checklistFromSnapshot,
  type AgentInsightCard,
  type ChecklistItem,
  type ClinicalFocus,
  type DashboardAgentState,
} from './collabTypes';
import type { ClinicalSnapshot } from '@/lib/types';
import { apiPatch } from '@/lib/api';

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

function parseJsonish(value: unknown): unknown {
  if (typeof value !== 'string') return value;
  try {
    return JSON.parse(value);
  } catch {
    return value;
  }
}

function toolCallName(tc: Record<string, unknown>): string {
  const fn = tc.function;
  if (fn && typeof fn === 'object') {
    const name = (fn as { name?: unknown }).name;
    if (typeof name === 'string') return name;
  }
  return String(tc.name ?? tc.toolName ?? '');
}

function toolCallArgs(tc: Record<string, unknown>): Record<string, unknown> {
  const fn = tc.function;
  if (fn && typeof fn === 'object') {
    const raw = (fn as { arguments?: unknown }).arguments;
    const parsed = parseJsonish(raw);
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      return parsed as Record<string, unknown>;
    }
  }
  const nested = parseJsonish(tc.args ?? tc.arguments ?? tc.input);
  if (nested && typeof nested === 'object' && !Array.isArray(nested)) {
    return nested as Record<string, unknown>;
  }
  return {};
}

/** CopilotKit often keeps tool args on messages even when custom LangGraph state keys don't sync. */
function extractUiFromMessages(messages: unknown[]): {
  insights: AgentInsightCard[];
  focus: ClinicalFocus | null;
  notes: Record<string, string>;
  assistantText: string;
} {
  let insights: AgentInsightCard[] = [];
  let focus: ClinicalFocus | null = null;
  let notes: Record<string, string> = {};
  let assistantText = '';

  for (const raw of messages) {
    if (!raw || typeof raw !== 'object') continue;
    const msg = raw as Record<string, unknown>;
    const role = String(msg.role ?? '');
    if (role === 'assistant' && typeof msg.content === 'string' && msg.content.trim()) {
      assistantText = msg.content.trim();
    }
    const toolCalls = (msg.toolCalls ?? msg.tool_calls ?? msg.calls) as unknown;
    if (role !== 'assistant' || !Array.isArray(toolCalls)) continue;
    for (const tcRaw of toolCalls) {
      if (!tcRaw || typeof tcRaw !== 'object') continue;
      const tc = tcRaw as Record<string, unknown>;
      if (toolCallName(tc) !== 'update_dashboard_ui') continue;
      const args = toolCallArgs(tc);
      const parsedInsights = asInsights(parseJsonish(args.insights_json));
      if (parsedInsights.length) insights = parsedInsights;
      const focusType = String(args.focus_type ?? '').trim();
      const focusKey = String(args.focus_key ?? '').trim();
      if (focusType && focusKey) {
        focus = asFocus({ type: focusType, key: focusKey });
      }
      const parsedNotes = parseJsonish(args.checklist_notes_json);
      if (parsedNotes && typeof parsedNotes === 'object' && !Array.isArray(parsedNotes)) {
        notes = Object.fromEntries(
          Object.entries(parsedNotes as Record<string, unknown>).map(([k, v]) => [k, String(v)]),
        );
      }
    }
  }
  return { insights, focus, notes, assistantText };
}

function synthesizeInsights(input: {
  insights: AgentInsightCard[];
  focus: ClinicalFocus | null;
  notes: Record<string, string>;
  assistantText: string;
}): AgentInsightCard[] {
  if (input.insights.length) return input.insights;
  const cards: AgentInsightCard[] = [];
  if (input.focus) {
    cards.push({
      id: `focus-${input.focus.type}-${input.focus.key}`,
      title: `Focus: ${input.focus.key}`,
      body:
        input.assistantText ||
        `Chart highlight set on ${input.focus.type} “${input.focus.key}”. Confirm against source records.`,
    });
  }
  const noteEntries = Object.entries(input.notes);
  if (!cards.length && noteEntries.length) {
    const [, note] = noteEntries[0];
    cards.push({
      id: 'checklist-note',
      title: 'Checklist note',
      body: note,
    });
  }
  if (!cards.length && input.assistantText) {
    cards.push({
      id: 'assistant-summary',
      title: 'Co-pilot update',
      body: input.assistantText,
    });
  }
  return cards;
}

/**
 * CopilotKit collaboration layer for the patient chart.
 * Must render under <CopilotKit agent="chart_router">.
 *
 * Local React state is the UI source of truth for checklist/insights/focus;
 * CopilotChat is the single doctor-facing chat (auto-routed specialists).
 */
export function DashboardCollabLayer({
  snapshot,
  onRefresh: _onRefresh,
  children,
}: {
  snapshot: ClinicalSnapshot;
  onRefresh: () => void;
  children: React.ReactNode;
}) {
  const [errorMsg, setErrorMsg] = useState('');
  const [runtimeOk, setRuntimeOk] = useState(true);
  const initialChecklist = () =>
    snapshot.doctor_checklist_items?.length
      ? checklistFromSnapshot(snapshot.doctor_checklist_items)
      : checklistFromSnapshot(
          snapshot.doctor_checklist.map((text, index) => ({
            id: `chk-${index}`,
            text,
            done: false,
            agent_note: null,
          })),
        );

  const [checklist, setChecklist] = useState<ChecklistItem[]>(initialChecklist);
  const [insights, setInsights] = useState<AgentInsightCard[]>([]);
  const [focus, setFocus] = useState<ClinicalFocus | null>(null);
  const seededRef = useRef(false);
  const prevRunning = useRef(false);

  const { agent } = useAgent({
    agentId: CHART_ROUTER_AGENT_ID,
    updates: [
      UseAgentUpdate.OnMessagesChanged,
      UseAgentUpdate.OnStateChanged,
      UseAgentUpdate.OnRunStatusChanged,
    ],
  });

  useEffect(() => {
    const controller = new AbortController();
    fetch('/api/copilotkit/info', { signal: controller.signal })
      .then((res) => setRuntimeOk(res.ok))
      .catch(() => setRuntimeOk(false));
    return () => controller.abort();
  }, []);

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
    const initial = initialChecklist();
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

  const applyRunResult = useCallback(() => {
    const state = (agent.state as DashboardAgentState | undefined) ?? {};
    const fromMessages = extractUiFromMessages(agent.messages as unknown[]);
    const syncedChecklist = asChecklist(state.checklist);
    const syncedFocus = asFocus(state.focus) ?? fromMessages.focus;
    const syncedInsights = synthesizeInsights({
      insights:
        asInsights(state.insights).length > 0
          ? asInsights(state.insights)
          : fromMessages.insights,
      focus: syncedFocus,
      notes: fromMessages.notes,
      assistantText: fromMessages.assistantText,
    });
    if (syncedChecklist.length) {
      const mergedChecklist = syncedChecklist.map((item) => ({
        ...item,
        agentNote: fromMessages.notes[item.id] ?? item.agentNote,
      }));
      setChecklist(mergedChecklist);
      for (const item of mergedChecklist) {
        if (!item.agentNote) continue;
        void apiPatch(`/api/patients/${snapshot.patient.id}/checklist/${item.id}`, {
          text: item.text,
          done: item.done,
          agent_note: item.agentNote,
        }).catch((error: unknown) =>
          setErrorMsg(error instanceof Error ? error.message : 'Could not save checklist note.'),
        );
      }
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
  }, [agent, snapshot.patient.id]);

  // When an agent run finishes, pull shared state into local UI (chart widgets).
  useEffect(() => {
    const running = agent.isRunning;
    const finished = prevRunning.current && !running;
    prevRunning.current = running;
    const state = (agent.state as DashboardAgentState | undefined) ?? {};
    const nextChecklist = asChecklist(state.checklist);
    const nextInsights = asInsights(state.insights);
    const nextFocus = asFocus(state.focus);

    if (nextChecklist.length) {
      setChecklist((prev) => {
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
    if (nextInsights.length) setInsights(nextInsights);
    if (nextFocus) setFocus(nextFocus);

    if (finished) {
      applyRunResult();
    }
  }, [agent.isRunning, agent.state, agent.messages, applyRunResult]);

  // Keep patient context fresh before CopilotChat sends (user typing path).
  useEffect(() => {
    if (!seededRef.current) return;
    pushStateToAgent({});
  }, [snapshot.patient.id, pushStateToAgent]);

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
        let lastErr: unknown;
        for (let attempt = 0; attempt < 3; attempt += 1) {
          try {
            await agent.runAgent();
            lastErr = null;
            break;
          } catch (err) {
            lastErr = err;
            const msg = err instanceof Error ? err.message : String(err);
            if (!/thread already running/i.test(msg) || attempt === 2) break;
            await new Promise((r) => setTimeout(r, 400 * (attempt + 1)));
          }
        }
        if (lastErr) throw lastErr;
        applyRunResult();
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Chart agent run failed';
        console.error('Chart agent run failed:', err);
        setErrorMsg(message);
        setRuntimeOk(false);
      } finally {
        runLockRef.current = false;
      }
    },
    [agent, applyRunResult, checklist, focus, insights, snapshot],
  );

  const toggleChecklistItem = useCallback(
    (id: string) => {
      setChecklist((prev) => {
        const current = prev.map((item) =>
          item.id === id ? { ...item, done: !item.done } : item,
        );
        const toggled = current.find((item) => item.id === id);
        pushStateToAgent({ checklist: current });
        if (toggled) {
          void apiPatch(`/api/patients/${snapshot.patient.id}/checklist/${toggled.id}`, {
            text: toggled.text,
            done: toggled.done,
            agent_note: toggled.agentNote ?? null,
          }).catch((error: unknown) =>
            setErrorMsg(error instanceof Error ? error.message : 'Could not save checklist item.'),
          );
        }
        if (toggled?.done) {
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
    [askAgent, pushStateToAgent, snapshot.patient.id],
  );

  const aside = (
    <div className="flex h-full min-h-0 flex-col">
      <div className="shrink-0 border-b border-border px-3 py-2">
        <p className="text-xs font-semibold text-slate-800">Clinical co-pilot</p>
        <p className="mt-0.5 text-[10px] leading-4 text-slate-500">
          One chat — auto-routes to chart UI updates or clinical memory. Non-diagnostic CDS aid.
          Upload documents from Memory Sources on the chart.
        </p>
        {!runtimeOk ? (
          <p className="mt-2 rounded-md border border-amber-200 bg-amber-50 px-2 py-1.5 text-[10px] leading-4 text-amber-900">
            CopilotKit runtime unreachable. Start <code className="font-mono">npm run dev:transcription</code>{' '}
            (ports 8010 + 4000), then refresh.
          </p>
        ) : null}
        {errorMsg ? (
          <p className="mt-2 rounded-md border border-red-200 bg-red-50 px-2 py-1.5 text-[10px] text-red-700">
            {errorMsg}
          </p>
        ) : null}
      </div>
      <div className="min-h-0 flex-1 [&_.copilotKitChat]:h-full">
        <CopilotChat
          agentId={CHART_ROUTER_AGENT_ID}
          className="h-full border-0"
          labels={{
            welcomeMessageText: `Ask about ${snapshot.patient.name}'s chart, or mark checklist items done for UI insights.`,
            chatInputPlaceholder: 'Ask about the chart or checklist…',
          }}
        />
      </div>
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [
      agent.isRunning,
      askAgent,
      checklist,
      focus,
      insights,
      toggleChecklistItem,
      errorMsg,
      runtimeOk,
      snapshot.patient.name,
    ],
  );

  return <DashboardCollabProvider value={collabApi}>{children}</DashboardCollabProvider>;
}
