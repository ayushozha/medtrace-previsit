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

export const DASHBOARD_AGENT_ID = 'dashboard_clinical';

export function checklistFromSnapshot(items: string[]): ChecklistItem[] {
  return items.map((text, index) => ({
    id: `chk-${index}`,
    text,
    done: false,
  }));
}
