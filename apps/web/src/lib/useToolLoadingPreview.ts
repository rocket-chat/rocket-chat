"use client";

import { useEffect, useMemo, useState } from "react";

export interface ToolLoadingPreview {
  /** The core tools: sent with every step whenever the agent may use them. */
  core: string[];
  /** Tools sent with every step: core, kept active, and the loading tools. */
  sent: number;
  total: number;
  deferred: string[];
  /** Above this many tools sent, the page warns. */
  warning_at: number;
  reason: string;
}

interface Input {
  activeTools: string[];
  toolNames: string[];
}

/**
 * Which of an agent's tools are active (sent with every step) and which are deferred, as the API
 * decides it. The rule lives on the server, so what the page shows is what a turn does.
 */
export function useToolLoadingPreview({ activeTools, toolNames }: Input) {
  const [preview, setPreview] = useState<ToolLoadingPreview | null>(null);
  const [failed, setFailed] = useState(false);
  const key = useMemo(
    () => JSON.stringify([activeTools, toolNames]),
    [activeTools, toolNames],
  );

  useEffect(() => {
    const controller = new AbortController();
    // Typing in the pin list changes the key on every keystroke: wait for a pause.
    const timer = setTimeout(() => {
      fetch("/v1/tools/loading-preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ active_tools: activeTools, tool_names: toolNames }),
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
    // `key` stands for the two inputs, which are arrays recreated on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  const deferred = useMemo(() => new Set(preview?.deferred ?? []), [preview]);
  return { preview, deferred, failed };
}
