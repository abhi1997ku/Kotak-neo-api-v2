import React, { createContext, useContext, useState, useCallback, useEffect, useRef } from "react";
import { apiClient } from "../services/api";

interface AuthContextType {
  isAuthenticated: boolean;
  isInitializing: boolean;
  isLoading: boolean;
  sessionHealth: "checking" | "active" | "degraded" | "expired" | "not_authenticated";
  error: string | null;
  login: (totp: string, mpin: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [isInitializing, setIsInitializing] = useState(true);
  const [isLoading, setIsLoading] = useState(false);
  const [sessionHealth, setSessionHealth] = useState<AuthContextType["sessionHealth"]>("checking");
  const [error, setError] = useState<string | null>(null);
  const authVersion = useRef(0);
  const wasAuthenticated = useRef(false);

  useEffect(() => {
    let active = true;

    const checkStatus = (reportExpiredSession = false) => {
      const requestVersion = authVersion.current;
      apiClient.getAuthStatus()
        .then((status: { authenticated?: boolean; session_health?: AuthContextType["sessionHealth"] }) => {
          if (active && requestVersion === authVersion.current) {
            const authenticated = Boolean(status.authenticated);
            setIsAuthenticated(authenticated);
            setSessionHealth(status.session_health || (authenticated ? "active" : "not_authenticated"));
            if (authenticated) {
              wasAuthenticated.current = true;
              setError(null);
            } else {
              if (reportExpiredSession || wasAuthenticated.current || status.session_health === "expired") {
                setError("Kotak session ended. Please log in again.");
              }
              wasAuthenticated.current = false;
            }
          }
        })
        // A failed status request can be a brief network/server problem. Keep
        // the last confirmed auth state until the backend explicitly says the
        // session is no longer active.
        .catch(() => {})
        .finally(() => {
          if (active) setIsInitializing(false);
        });
    };

    const handleUnauthorized = () => checkStatus(true);
    window.addEventListener("kotak:unauthorized", handleUnauthorized);

    checkStatus();
    const timer = window.setInterval(() => checkStatus(), 10000);

    return () => {
      active = false;
      window.clearInterval(timer);
      window.removeEventListener("kotak:unauthorized", handleUnauthorized);
    };
  }, []);

  const login = useCallback(async (totp: string, mpin: string) => {
    authVersion.current += 1;
    setIsLoading(true);
    setError(null);

    try {
      await apiClient.login(totp, mpin);
      wasAuthenticated.current = true;
      setIsAuthenticated(true);
      setSessionHealth("active");
      setError(null);
    } catch (err) {
      const rawMessage = err instanceof Error ? err.message : "Login failed";
      const normalized = rawMessage.toLowerCase().includes("totp") || rawMessage.toLowerCase().includes("mpin") || rawMessage.toLowerCase().includes("invalid")
        ? "Broker rejected the login: Invalid TOTP / MPIN. Generate a fresh TOTP from your Kotak app and try again."
        : rawMessage;
      setError(normalized);
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, []);

  const logout = useCallback(async () => {
    authVersion.current += 1;
    wasAuthenticated.current = false;
    try {
      await apiClient.logout();
    } finally {
      setIsAuthenticated(false);
      setSessionHealth("not_authenticated");
      setError(null);
    }
  }, []);

  return (
    <AuthContext.Provider value={{ isAuthenticated, isInitializing, isLoading, sessionHealth, error, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used within AuthProvider");
  }
  return context;
}
