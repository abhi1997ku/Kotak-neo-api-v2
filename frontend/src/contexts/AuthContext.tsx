import React, { createContext, useContext, useState, useCallback } from "react";
import { apiClient } from "../services/api";

interface AuthContextType {
  isAuthenticated: boolean;
  isLoading: boolean;
  error: string | null;
  login: (totp: string, mpin: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const login = useCallback(async (totp: string, mpin: string) => {
    setIsLoading(true);
    setError(null);

    try {
      await apiClient.login(totp, mpin);
      setIsAuthenticated(true);
      apiClient.setToken(`${totp}:${mpin}`);
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

  const logout = useCallback(() => {
    setIsAuthenticated(false);
    apiClient.clearToken();
    setError(null);
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
