import React, { createContext, useContext, useState, useCallback, useEffect, useRef } from "react";
import { apiClient } from "../services/api";

interface AuthContextType {
  isAuthenticated: boolean;
  isLoading: boolean;
  error: string | null;
  login: (totp: string, mpin: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const authVersion = useRef(0);

  useEffect(() => {
    let active = true;

    const handleUnauthorized = () => {
      if (active) {
        setIsAuthenticated(false);
        setError("Session expired. Please log in again.");
      }
    };
    window.addEventListener("kotak:unauthorized", handleUnauthorized);

    const checkStatus = () => {
      const requestVersion = authVersion.current;
      apiClient.getAuthStatus()
        .then((status: { authenticated?: boolean }) => {
          if (active && requestVersion === authVersion.current) {
            setIsAuthenticated(Boolean(status.authenticated));
          }
        })
        .catch(() => {
          if (active) {
            setIsAuthenticated(false);
          }
        });
    };

    checkStatus();
    const timer = window.setInterval(checkStatus, 10000);

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
      setIsAuthenticated(true);
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
    try {
      await apiClient.logout();
    } finally {
      setIsAuthenticated(false);
      setError(null);
    }
  }, []);

  return (
    <AuthContext.Provider value={{ isAuthenticated, isLoading, error, login, logout }}>
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
