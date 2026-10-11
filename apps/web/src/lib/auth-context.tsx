"use client";

import React, { createContext, useContext, useEffect, useState, useCallback, useMemo } from "react";

export interface AuthUser {
  id: string;
  user_id: string;
  email: string;
  name: string;
  org_id: string;
  roles: string[];
  groups?: string[];
  accessToken?: string;
}

interface AuthContextType {
  user: AuthUser | null;
  isLoading: boolean;
  isAuthenticated: boolean;
  refreshUser: () => Promise<AuthUser | null>;
  logout: (redirectUrl?: string) => Promise<void>;
}

const AuthContext = createContext<AuthContextType>({
  user: null,
  isLoading: true,
  isAuthenticated: false,
  refreshUser: async () => null,
  logout: async () => {},
});

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const fetchCurrentUser = useCallback(async (): Promise<AuthUser | null> => {
    try {
      const res = await fetch("/api/auth/me", {
        headers: { Accept: "application/json" },
        cache: "no-store",
      });
      if (res.ok) {
        const data: AuthUser = await res.json();
        setUser(data);
        return data;
      }
      setUser(null);
      return null;
    } catch (err) {
      console.warn("[Auth] Failed to resolve active session:", err);
      setUser(null);
      return null;
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchCurrentUser();
  }, [fetchCurrentUser]);

  const logout = useCallback(async (redirectUrl: string = "/login") => {
    try {
      await fetch("/api/auth/logout", { method: "POST" });
    } catch (err) {
      console.warn("[Auth] Logout request error:", err);
    } finally {
      setUser(null);
      window.location.href = redirectUrl;
    }
  }, []);

  const contextValue = useMemo(
    () => ({
      user,
      isLoading,
      isAuthenticated: !!user,
      refreshUser: fetchCurrentUser,
      logout,
    }),
    [user, isLoading, fetchCurrentUser, logout]
  );

  return (
    <AuthContext.Provider value={contextValue}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}

/**
 * Drop-in backward compatibility helper for components migrating from next-auth useSession()
 */
export function useSession() {
  const { user, isLoading } = useAuth();

  const sessionData = useMemo(() => {
    if (!user) return null;
    return {
      user: {
        id: user.user_id,
        name: user.name,
        email: user.email,
        orgId: user.org_id,
        roles: user.roles,
        accessToken: user.accessToken,
      },
    };
  }, [user]);

  return useMemo(
    () => ({
      data: sessionData,
      status: (isLoading ? "loading" : user ? "authenticated" : "unauthenticated") as
        | "loading"
        | "authenticated"
        | "unauthenticated",
    }),
    [sessionData, isLoading, user]
  );
}

export function signOut(options?: { callbackUrl?: string }) {
  fetch("/api/auth/logout", { method: "POST" })
    .catch(() => {})
    .finally(() => {
      window.location.href = options?.callbackUrl || "/login";
    });
}
