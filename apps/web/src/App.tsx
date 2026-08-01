import { Suspense, lazy } from 'react';
import { Loader2 } from 'lucide-react';
import { Navigate, Route, Routes, useNavigate, useParams } from 'react-router-dom';
import { AppNav } from './components/AppNav';
import { DashboardHome } from './components/DashboardHome';
import { MainDashboard } from './components/MainDashboard';
import { ImagingWorkspace } from './components/imaging/ImagingWorkspace';

// CopilotKit + tiptap pull in ~2 MB of syntax-highlighting and diagram code. Loading the
// session route on demand keeps that out of the patients and imaging bundles.
const SessionWorkspace = lazy(() =>
  import('./components/session/SessionWorkspace').then((m) => ({ default: m.SessionWorkspace })),
);

function RouteFallback() {
  return (
    <div className="grid min-h-[60vh] place-items-center text-slate-500">
      <Loader2 className="h-6 w-6 animate-spin" />
    </div>
  );
}

function PatientDirectoryRoute() {
  const navigate = useNavigate();
  return <MainDashboard onSelectPatient={(id) => navigate(`/patients/${id}`)} />;
}

function PatientChartRoute() {
  const { patientId } = useParams<{ patientId: string }>();
  const navigate = useNavigate();
  if (!patientId) return <Navigate to="/" replace />;
  return <DashboardHome patientId={patientId} onBack={() => navigate('/')} />;
}

export default function App() {
  return (
    <div className="min-h-screen bg-background text-foreground">
      <AppNav />
      <main>
        <Routes>
          <Route path="/" element={<PatientDirectoryRoute />} />
          <Route path="/patients/:patientId" element={<PatientChartRoute />} />
          <Route path="/imaging" element={<ImagingWorkspace />} />
          <Route
            path="/session"
            element={
              <Suspense fallback={<RouteFallback />}>
                <SessionWorkspace />
              </Suspense>
            }
          />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}
