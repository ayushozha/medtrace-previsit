import { createContext, useContext } from 'react';
import type { AgentInsightCard, ChecklistItem, ClinicalFocus } from './collabTypes';

export interface DashboardCollabApi {
  checklist: ChecklistItem[];
  insights: AgentInsightCard[];
  focus: ClinicalFocus | null;
  isAgentRunning: boolean;
  toggleChecklistItem: (id: string) => void;
  askAgent: (text: string) => Promise<void>;
  aside: React.ReactNode;
}

const DashboardCollabContext = createContext<DashboardCollabApi | null>(null);

export function DashboardCollabProvider({
  value,
  children,
}: {
  value: DashboardCollabApi;
  children: React.ReactNode;
}) {
  return (
    <DashboardCollabContext.Provider value={value}>{children}</DashboardCollabContext.Provider>
  );
}

export function useDashboardCollab(): DashboardCollabApi | null {
  return useContext(DashboardCollabContext);
}
