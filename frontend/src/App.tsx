import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider, useAuth } from "./contexts/AuthContext";
import { LoginForm } from "./components/LoginForm";
import { Dashboard } from "./components/Dashboard";
import { useEffect, useState, lazy, Suspense } from "react";
import { isResearchWorkspace } from "./services/workspace";
const OrderBlockPaperPanel = lazy(() => import("./components/OrderBlockPaperPanel"));
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

  return isAuthenticated ? <Suspense fallback={<p>Loading workspace...</p>}><Dashboard /></Suspense> : <>
    <div className="bg-slate-900 p-3 text-center text-sm text-slate-300">
      Live trading login
    </div>
    <LoginForm />
  </>;
}

function App() {
  const [swingPaper, setSwingPaper] = useState(window.location.hash === "#stock-paper");
  useEffect(() => {
    const update = () => setSwingPaper(window.location.hash === "#stock-paper");
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, []);

  return (
    <QueryClientProvider client={queryClient}>
      {isResearchWorkspace ? (
        <main className="min-h-screen bg-slate-950 p-4 md:p-8">
          <div className="mx-auto max-w-7xl">
            <nav aria-label="Paper trading navigation" className="mb-4 flex flex-wrap gap-4 text-sm text-slate-400">
              <span>Paper trading &amp; research</span>
              <a href="#order-block-paper" aria-current={!swingPaper ? "page" : undefined} className={!swingPaper ? "text-emerald-300" : "hover:text-slate-100"}>Order block paper</a>
              <a href="#stock-paper" aria-current={swingPaper ? "page" : undefined} className={swingPaper ? "text-emerald-300" : "hover:text-slate-100"}>Swing trades</a>
              <a href="http://localhost:5173/">Live terminal</a>
            </nav>
            <Suspense fallback={<p className="text-slate-300">Loading paper workspace…</p>}>
              {swingPaper ? <ScreenerPanel /> : <OrderBlockPaperPanel />}
            </Suspense>
          </div>
        </main>
      ) : (
        <AuthProvider><AppContent /></AuthProvider>
      )}
    </QueryClientProvider>
  );
}

export default App;
