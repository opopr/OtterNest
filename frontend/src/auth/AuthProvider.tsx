import {
  createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode
} from "react";
import { getApiOrigin, request, setAccessToken } from "../api/client";
import { formatAuthConnectionError } from "../api/url";
import type { AuthStatus, OwnerActor } from "../api/types";

interface AuthResponse {
  status: "authenticated" | "mfa_required";
  token?: string;
  challenge_token?: string;
}
interface AuthContextValue {
  authStatus: AuthStatus | null;
  authError: string;
  checking: boolean;
  token: string | null;
  user: OwnerActor | null;
  isAuthenticated: boolean;
  refreshAuthStatus: () => Promise<void>;
  bootstrap: (input: {
    owner_token: string; username: string; password: string; display_name: string;
  }) => Promise<void>;
  loginWithPassword: (username: string, password: string) => Promise<string | null>;
  verifyMfa: (challengeToken: string, code: string) => Promise<void>;
  loginWithToken: (value: string) => Promise<void>;
  logout: () => Promise<void>;
}
const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [authStatus, setAuthStatus] = useState<AuthStatus | null>(null);
  const [authError, setAuthError] = useState("");
  const [checking, setChecking] = useState(true);
  const [token, setToken] = useState<string | null>(null);
  const [user, setUser] = useState<OwnerActor | null>(null);

  const refreshAuthStatus = useCallback(async () => {
    setChecking(true);
    setAuthError("");
    try {
      setAuthStatus(await request<AuthStatus>("/auth/status", {}, null));
    } catch (reason) {
      setAuthStatus(null);
      setAuthError(formatAuthConnectionError(reason, getApiOrigin()));
    } finally {
      setChecking(false);
    }
  }, []);

  useEffect(() => {
    void refreshAuthStatus();
  }, [refreshAuthStatus]);

  const establishSession = useCallback(async (newToken: string) => {
    setAccessToken(newToken);
    try {
      const actor = await request<OwnerActor>("/me");
      setToken(newToken);
      setUser(actor);
    } catch (error) {
      setAccessToken(null);
      setToken(null);
      setUser(null);
      throw error;
    }
  }, []);

  const bootstrap = useCallback(async (input: {
    owner_token: string; username: string; password: string; display_name: string;
  }) => {
    const result = await request<AuthResponse>(
      "/auth/bootstrap",
      { method: "POST", body: JSON.stringify(input) },
      null
    );
    if (!result.token) throw new Error("账号初始化没有返回有效会话");
    await establishSession(result.token);
    await refreshAuthStatus();
  }, [establishSession, refreshAuthStatus]);

  const loginWithPassword = useCallback(async (username: string, password: string) => {
    const result = await request<AuthResponse>(
      "/auth/login",
      { method: "POST", body: JSON.stringify({ username, password }) },
      null
    );
    if (result.status === "mfa_required") return result.challenge_token || "";
    if (!result.token) throw new Error("登录响应缺少会话令牌");
    await establishSession(result.token);
    return null;
  }, [establishSession]);

  const verifyMfa = useCallback(async (challengeToken: string, code: string) => {
    const result = await request<{ token: string }>(
      "/auth/2fa/verify",
      { method: "POST", body: JSON.stringify({ challenge_token: challengeToken, code }) },
      null
    );
    await establishSession(result.token);
  }, [establishSession]);

  const loginWithToken = useCallback(async (value: string) => {
    await establishSession(value);
  }, [establishSession]);

  const logout = useCallback(async () => {
    const current = token;
    if (current && current.startsWith("on_session_")) {
      try {
        await request("/auth/logout", { method: "POST" }, current);
      } catch {
        // Local logout still completes when the server session has expired.
      }
    }
    setAccessToken(null);
    setToken(null);
    setUser(null);
  }, [token]);

  const value = useMemo<AuthContextValue>(() => ({
    authStatus, authError, checking, token, user, isAuthenticated: Boolean(token && user),
    refreshAuthStatus, bootstrap, loginWithPassword, verifyMfa, loginWithToken, logout
  }), [authStatus, authError, checking, token, user, refreshAuthStatus, bootstrap, loginWithPassword, verifyMfa, loginWithToken, logout]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth 必须在 AuthProvider 内使用");
  return context;
}
