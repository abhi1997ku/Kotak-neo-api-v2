import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider, useAuth } from "./contexts/AuthContext";
import { LoginForm } from "./components/LoginForm";
import { Dashboard } from "./components/Dashboard";
import { useEffect, useState, lazy, Suspense } from "react";
import { isResearchWorkspace } from "./services/workspace";
const ResearchDashboard = lazy(() => import("./components/ResearchDashboard"));
const EmaVwapPaperPanel = lazy(() => import("./components/EmaVwapPaperPanel"));
const ScreenerPanel = lazy(() => import("./components/ScreenerPanel").then(module => ({ default: module.ScreenerPanel })));

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

function AppContent() {
  const { isAuthenticated, isInitializing } = useAuth();

  if (isInitializing) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-950 text-sm text-slate-400">
        Checking Kotak session...
      </div>
    );
  }

  return isAuthenticated ? <Suspense fallback={<p>Loading workspace...</p>}>{isResearchWorkspace ? <ResearchDashboard /> : <Dashboard />}</Suspense> : <>
    <div className="bg-slate-900 p-3 text-center text-sm text-slate-300">
      {isResearchWorkspace ? "Paper trading & research login" : "Live trading login"}
    </div>
    <LoginForm />
  </>;
}

function App() {
  const [emaPaper, setEmaPaper] = useState(isResearchWorkspace);
  const [paperOnly, setPaperOnly] = useState(window.location.hash === "#stock-paper");
  useEffect(() => {
    const update = () => { setPaperOnly(window.location.hash === "#stock-paper"); setEmaPaper(isResearchWorkspace); };
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, []);
  if (emaPaper) return <QueryClientProvider client={queryClient}><main className="min-h-screen bg-slate-950 p-4 md:p-8"><div className="mx-auto max-w-7xl"><nav className="mb-4 flex gap-4 text-sm text-slate-400"><span>INDstocks paper workspace</span><a href="http://localhost:5173/">Live terminal</a></nav><Suspense fallback={<p className="text-slate-300">Loading paper monitor…</p>}><EmaVwapPaperPanel /></Suspense></div></main></QueryClientProvider>;
  return (
    <QueryClientProvider client={queryClient}>
      {paperOnly ? <main className="min-h-screen bg-slate-950 p-4 md:p-8"><div className="mx-auto max-w-6xl"><a href="#" className="mb-4 inline-block text-sm text-slate-400 underline">Back to terminal</a><Suspense fallback={<p>Loading research...</p>}><ScreenerPanel /></Suspense></div></main> : <AuthProvider>
        <AppContent />
      </AuthProvider>}
    </QueryClientProvider>
  );
}

export default App;
