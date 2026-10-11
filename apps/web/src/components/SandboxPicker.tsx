"use client";

import React, { useEffect, useState } from "react";
import { Box } from "lucide-react";
import { useMissionStore } from "../lib/store";

interface Sandbox {
  id: string;
  name: string;
  description: string;
  capabilities: string[];
  is_default: boolean;
}

/**
 * Chooses the sandbox a NEW session starts in. Shown only when the organization offers more
 * than one; a session keeps the sandbox it started with.
 */
export const SandboxPicker: React.FC<{ disabled?: boolean }> = ({ disabled }) => {
  const selected = useMissionStore((s) => s.selectedSandboxId);
  const select = useMissionStore((s) => s.setSelectedSandboxId);
  const [sandboxes, setSandboxes] = useState<Sandbox[]>([]);

  useEffect(() => {
    let cancelled = false;
    fetch("/v1/sandboxes")
      .then((res) => (res.ok ? res.json() : null))
      .then((body) => {
        if (!cancelled && body?.sandboxes) setSandboxes(body.sandboxes);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  if (sandboxes.length < 2) return null;
  const current = sandboxes.find((s) => s.id === selected);
  const hint = current
    ? [current.description, current.capabilities.length ? `Can: ${current.capabilities.join(", ")}` : ""]
        .filter(Boolean)
        .join(" - ")
    : "The organization's default sandbox.";

  return (
    <label
      className="flex items-center gap-1.5 px-2 py-1.5 rounded-lg bg-surface border border-border text-muted-foreground focus-within:border-primary/60"
      title={hint}
    >
      <Box className="w-3.5 h-3.5" aria-hidden="true" />
      <span className="sr-only">Sandbox</span>
      <select
        data-testid="sandbox-picker"
        aria-label="Sandbox"
        value={selected}
        disabled={disabled}
        onChange={(e) => select(e.target.value)}
        className="bg-transparent text-xs text-foreground focus:outline-none cursor-pointer max-w-[9rem]"
      >
        <option value="">Default</option>
        {sandboxes.map((box) => (
          <option key={box.id} value={box.id}>
            {box.name}
          </option>
        ))}
      </select>
    </label>
  );
};
