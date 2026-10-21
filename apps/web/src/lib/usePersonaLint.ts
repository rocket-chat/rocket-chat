"use client";

import { useEffect, useState } from "react";

export interface PersonaWarning {
  kind: "too_long" | "unknown_tool" | "unknown_argument";
  message: string;
}

/**
 * What the API finds wrong with an agent's prompt (too long, a tool or an argument that does not
 * exist), checked after a pause in typing. Warnings only: a save is never refused for them.
 */
export function usePersonaLint(systemPrompt: string): PersonaWarning[] {
  const [warnings, setWarnings] = useState<PersonaWarning[]>([]);

  useEffect(() => {
    const controller = new AbortController();
    const timer = setTimeout(() => {
      fetch("/v1/agents/lint", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ system_prompt: systemPrompt }),
        signal: controller.signal,
      })
        .then((res) => (res.ok ? res.json() : Promise.reject(new Error(String(res.status)))))
        .then((data: { warnings: PersonaWarning[] }) => setWarnings(data.warnings))
        // The check is advice: when it cannot run, the editor simply shows none.
        .catch(() => undefined);
    }, 500);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [systemPrompt]);

  return warnings;
}
