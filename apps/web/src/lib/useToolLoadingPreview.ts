"use client";

import { useEffect, useMemo, useState } from "react";

export interface ToolLoadingPreview {
  mode: "all" | "on_demand" | "pinned" | "auto";
  source: "agent" | "organization";
  threshold: number;
  total: number;
  deferred: string[];
  reason: string;
}

interface Input {
  toolLoading: string | null;
  activeTools: string[];
  toolNames: string[];
}

/**
 * Which of an agent's tools are active (sent with every step) and which are deferred, as the API
 * would decide it for these settings and the organization's current ones. The rule lives on the
 * server, so what the page shows is what a turn does.
 */
export function useToolLoadingPreview({ toolLoading, activeTools, toolNames }: Input) {
  const [preview, setPreview] = useState<ToolLoadingPreview | null>(null);
  const [failed, setFailed] = useState(false);
  const key = useMemo(
    () => JSON.stringify([toolLoading, activeTools, toolNames]),
    [toolLoading, activeTools, toolNames],
  );

  useEffect(() => {
    const controller = new AbortController();
    // Typing in the pin list changes the key on every keystroke: wait for a pause.
    const timer = setTimeout(() => {
      fetch("/v1/tools/loading-preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tool_loading: toolLoading || null, active_tools: activeTools, tool_names: toolNames }),
        signal: controller.signal,
      })
        .then((res) => (res.ok ? res.json() : Promise.reject(new Error(String(res.status)))))
        .then((data: ToolLoadingPreview) => {
          setPreview(data);
          setFailed(false);
        })
        .catch((err: unknown) => {
          if (!(err instanceof DOMException && err.name === "AbortError")) setFailed(true);
        });
    }, 250);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
    // `key` stands for the three inputs, which are arrays recreated on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  const deferred = useMemo(() => new Set(preview?.deferred ?? []), [preview]);
  return { preview, deferred, failed };
}
