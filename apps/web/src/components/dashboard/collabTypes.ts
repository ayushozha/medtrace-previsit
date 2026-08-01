import type { ChecklistItemRecord } from '@/lib/types';

export type ClinicalFocusType = 'lab' | 'condition' | 'med' | 'alert';

export interface ChecklistItem {
  id: string;
  text: string;
  done: boolean;
  agentNote?: string;
}

export interface AgentInsightCard {
  id: string;
  title: string;
  body: string;
  relatedChecklistId?: string;
}

export interface ClinicalFocus {
  type: ClinicalFocusType;
  key: string;
}

export interface DashboardAgentState {
  patient_id?: string;
  patient_context?: string;
  checklist?: ChecklistItem[];
  insights?: AgentInsightCard[];
  focus?: ClinicalFocus | null;
}

/** @deprecated Prefer CHART_ROUTER_AGENT_ID — kept for direct specialist debugging. */
export const DASHBOARD_AGENT_ID = 'dashboard_clinical';

/** Patient-chart CopilotKit auto-router (collab UI + clinical memory). */
export const CHART_ROUTER_AGENT_ID = 'chart_router';

export function checklistFromSnapshot(items: ChecklistItemRecord[]): ChecklistItem[] {
  return items.map((item) => ({
    id: item.id,
    text: item.text,
    done: item.done,
    ...(item.agent_note ? { agentNote: item.agent_note } : {}),
  }));
}
