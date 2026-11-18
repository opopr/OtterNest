import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { request } from "../api/client";
import type { SystemMode } from "../api/types";
import { useAuth } from "./AuthProvider";

interface SystemModeContextValue {
  systemMode: SystemMode | null;
  refreshSystemMode: () => Promise<void>;
}

const SystemModeContext = createContext<SystemModeContextValue>({
  systemMode: null,
  refreshSystemMode: async () => undefined
});

export function SystemModeProvider({ children }: { children: ReactNode }) {
  const { isAuthenticated } = useAuth();
  const [systemMode, setSystemMode] = useState<SystemMode | null>(null);

  const refreshSystemMode = useCallback(async () => {
    if (!isAuthenticated) {
      setSystemMode(null);
      return;
    }
    try {
      const next = await request<SystemMode>("/system/mode");
      setSystemMode(next);
    } catch {
      setSystemMode(null);
    }
  }, [isAuthenticated]);

  useEffect(() => {
    void refreshSystemMode();
  }, [refreshSystemMode]);

  return (
    <SystemModeContext.Provider value={{ systemMode, refreshSystemMode }}>
      {children}
    </SystemModeContext.Provider>
  );
}

export function useSystemMode(): SystemModeContextValue {
  return useContext(SystemModeContext);
}
