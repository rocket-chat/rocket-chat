"use client";

import { useEffect, useState } from "react";
import type { SessionData } from "../types/mission";
import type { SessionsScope } from "./store/types";

interface SessionSearchOptions {
  enabled?: boolean;
  limit?: number;
  archived?: boolean;
  scope?: SessionsScope;
}

/**
 * Conversations whose title matches `query`, asked of the server (`GET /v1/sessions?q=`) so the
 * search covers every conversation, not only the page the sidebar has loaded. One request per
 * pause in typing; an older answer never overwrites a newer one.
 */
export function useSessionSearch(query: string, { enabled = true, limit = 20, archived = false, scope = "mine" }: SessionSearchOptions = {}): SessionData[] {
  const [found, setFound] = useState<SessionData[]>([]);
  useEffect(() => {
    const q = query.trim();
    if (!enabled || !q) {
      setFound([]);
      return;
    }
    const controller = new AbortController();
    const timer = setTimeout(() => {
      const params = new URLSearchParams({ q, limit: String(limit), scope });
      if (archived) params.set("archived", "true");
      fetch(`/v1/sessions?${params.toString()}`, { signal: controller.signal })
        .then((res) => (res.ok ? res.json() : []))
        .then((body: unknown) => setFound(Array.isArray(body) ? (body as SessionData[]) : []))
        .catch(() => undefined);
    }, 200);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [query, enabled, limit, archived, scope]);
  return found;
}
