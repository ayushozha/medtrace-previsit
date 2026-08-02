import { Navigate, useNavigate, useParams } from 'react-router-dom';
import { CopilotKit } from '@copilotkit/react-core';
import { CopilotChatConfigurationProvider } from '@copilotkit/react-core/v2';

import '@copilotkit/react-core/v2/styles.css';

import { DashboardHome } from '../DashboardHome';
import { CHART_ROUTER_AGENT_ID } from './collabTypes';

/** Lazy-loaded patient chart with unified CopilotKit auto-router chat. */
export function PatientChartWorkspace() {
  const { patientId } = useParams<{ patientId: string }>();
  const navigate = useNavigate();
  if (!patientId) return <Navigate to="/patients" replace />;

  return (
    <CopilotKit
      key={patientId}
      runtimeUrl="/api/copilotkit"
      useSingleEndpoint={false}
      showDevConsole={import.meta.env.DEV}
      agent={CHART_ROUTER_AGENT_ID}
    >
      <CopilotChatConfigurationProvider agentId={CHART_ROUTER_AGENT_ID}>
        <DashboardHome
          patientId={patientId}
          onBack={() => navigate('/patients')}
          collabMode
        />
      </CopilotChatConfigurationProvider>
    </CopilotKit>
  );
}
