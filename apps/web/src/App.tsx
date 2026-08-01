import { Suspense, lazy } from 'react';
import { Loader2 } from 'lucide-react';
import { Navigate, Route, Routes, useNavigate, useParams } from 'react-router-dom';
import { AppNav } from './components/AppNav';
import { LandingPage } from './components/LandingPage';
import { MainDashboard } from './components/MainDashboard';
import { ImagingWorkspace } from './components/imaging/ImagingWorkspace';

// CopilotKit pulls in a heavy runtime. Lazy-load session + patient-chart collab so
// the directory and imaging routes stay light.
const SessionWorkspace = lazy(() =>
  import('./components/session/SessionWorkspace').then((m) => ({ default: m.SessionWorkspace })),
);
const PatientChartWorkspace = lazy(() =>
  import('./components/dashboard/PatientChartWorkspace').then((m) => ({
    default: m.PatientChartWorkspace,
  })),
);
const YcMedplumHackathonDemo = lazy(() =>
  import('./components/demo/YcMedplumHackathonDemo').then((m) => ({
    default: m.YcMedplumHackathonDemo,
  })),
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

function PatientImagingRoute() {
  const { patientId } = useParams<{ patientId: string }>();
  if (!patientId) return <Navigate to="/patients" replace />;
  return <ImagingWorkspace patientId={patientId} />;
}

function PatientSessionRoute() {
  const { patientId } = useParams<{ patientId: string }>();
  if (!patientId) return <Navigate to="/patients" replace />;
  return (
    <Suspense fallback={<RouteFallback />}>
      <SessionWorkspace patientId={patientId} />
    </Suspense>
  );
}

export default function App() {
  return (
    <div className="min-h-screen bg-background text-foreground">
      <AppNav />
      <main>
        <Routes>
          <Route path="/" element={<LandingPage />} />
          <Route path="/patients" element={<PatientDirectoryRoute />} />
          <Route
            path="/patients/:patientId"
            element={
              <Suspense fallback={<RouteFallback />}>
                <PatientChartWorkspace />
              </Suspense>
            }
          />
          <Route path="/patients/:patientId/imaging" element={<PatientImagingRoute />} />
          <Route path="/patients/:patientId/session" element={<PatientSessionRoute />} />
          {/* Legacy top-level routes — imaging/session require a patient context. */}
          <Route path="/imaging" element={<Navigate to="/patients" replace />} />
          <Route path="/session" element={<Navigate to="/patients" replace />} />
          <Route
            path="/yc-medplum-hackathon-demo"
            element={
              <Suspense fallback={<RouteFallback />}>
                <YcMedplumHackathonDemo />
              </Suspense>
            }
          />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}
