"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { SessionUsage } from "./session-usage";
import { useMissionStore } from "./store";

const POLL_MS = 8000;

/**
 * The session's tokens and cost. Read when the session opens, again every few seconds while a turn
 * runs (the ledger is written call by call, so the figure climbs live) and once more when the turn
 * ends, which is when the calls that title and summarise it have been made.
 */
export function useSessionUsage(sessionId: string | undefined, running: boolean) {
  const [usage, setUsage] = useState<SessionUsage | null>(null);
  const wasRunning = useRef(false);
  const runningRef = useRef(running);
  runningRef.current = running;

  const load = useCallback(
    async (signal?: AbortSignal) => {
      if (!sessionId) return;
      try {
        const res = await fetch(`/v1/sessions/${sessionId}/usage`, { signal });
        if (res.ok) {
          const data = (await res.json()) as SessionUsage;
          setUsage(data);
          if (
            data.active_context &&
            typeof data.active_context.tokens === "number" &&
            typeof data.active_context.limit === "number"
          ) {
            const store = useMissionStore.getState();
            if (!runningRef.current || store.contextTokens == null) {
              store.setContextUsage(data.active_context.tokens, data.active_context.limit, {
                modelLimit: data.active_context.model_limit,
                targetTokens: data.active_context.target_tokens,
                source: data.active_context.source,
                tier2Tokens: data.active_context.tier2_tokens,
                tier3Tokens: data.active_context.tier3_tokens,
              });
            }
          }
        }
      } catch {
        // A missed read leaves the last figures: the next one corrects them.
      }
    },
    [sessionId],
  );

  useEffect(() => {
    setUsage(null);
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  useEffect(() => {
    if (!running) {
      if (wasRunning.current) void load();
      wasRunning.current = false;
      return;
    }
    wasRunning.current = true;
    const timer = setInterval(() => void load(), POLL_MS);
    return () => clearInterval(timer);
  }, [running, load]);

  return usage;
}
