import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { lazy, Suspense } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import { AuthProvider, useAuth } from "./lib/auth";
import Login from "./pages/Login";

const Audit = lazy(() => import("./pages/Audit"));
const Backups = lazy(() => import("./pages/Backups"));
const Copilot = lazy(() => import("./pages/Copilot"));
const GameDays = lazy(() => import("./pages/GameDays"));
const Incidents = lazy(() => import("./pages/Incidents"));
const Overview = lazy(() => import("./pages/Overview"));
const Runs = lazy(() => import("./pages/Runs"));
const Settings = lazy(() => import("./pages/Settings"));
const Simulator = lazy(() => import("./pages/Simulator"));

const qc = new QueryClient({ defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: true } } });

function Gate() {
  const { me, loading, can } = useAuth();
  if (loading) return <div className="grid h-full place-items-center text-ink-400">Loading…</div>;
  if (!me) return <Login />;
  return (
    <Suspense fallback={<div className="p-6 text-ink-400">Loading…</div>}>
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Overview />} />
        <Route path="runs" element={<Runs />} />
        <Route path="simulator" element={<Simulator />} />
        <Route path="gamedays" element={<GameDays />} />
        <Route path="backups" element={<Backups />} />
        <Route path="incidents" element={<Incidents />} />
        <Route path="copilot" element={<Copilot />} />
        {can("view_audit") && <Route path="audit" element={<Audit />} />}
        <Route path="settings" element={<Settings />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
    </Suspense>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={qc}>
      <AuthProvider>
        <BrowserRouter>
          <Gate />
        </BrowserRouter>
      </AuthProvider>
    </QueryClientProvider>
  );
}
