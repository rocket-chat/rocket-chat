"use client";

import React, { useEffect, useState } from "react";
import { Box } from "lucide-react";
import { useMissionStore } from "../lib/store";
import { useTeams } from "../lib/teams";

interface Sandbox {
  id: string;
  name: string;
  description: string;
  capabilities: string[];
  is_default: boolean;
  /** A team's type: only the team's members start sessions in it. */
  team_id?: string | null;
}

/**
 * Chooses the sandbox a NEW session starts in. Shown only when the organization offers more
 * than one; a session keeps the sandbox it started with. Leaving it on the first option uses
 * the agent's sandbox type, else the organization's default.
 */
export const SandboxPicker: React.FC<{ disabled?: boolean; bare?: boolean }> = ({ disabled, bare }) => {
  const selected = useMissionStore((s) => s.selectedSandboxId);
  const select = useMissionStore((s) => s.setSelectedSandboxId);
  // A direct-model turn runs no agent, so only an agent's own sandbox type replaces the default.
  const agentSandboxId = useMissionStore((s) =>
    s.inferenceMode === "agent" ? s.activeAgent?.sandbox_id || "" : ""
  );
  const [listed, setSandboxes] = useState<Sandbox[]>([]);
  const { teams } = useTeams();
  // An administrator is listed every team's types, but only a team's members may start one.
  const sandboxes = listed.filter((box) => !box.team_id || teams.some((t) => t.id === box.team_id && t.my_role !== null));
  const teamName = (box: Sandbox) => teams.find((t) => t.id === box.team_id)?.name;

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
  const agentSandbox = sandboxes.find((s) => s.id === agentSandboxId);
  const current = sandboxes.find((s) => s.id === selected);
  const hint = current
    ? [current.description, current.capabilities.length ? `Can: ${current.capabilities.join(", ")}` : ""]
        .filter(Boolean)
        .join(" - ")
    : agentSandbox
      ? `The agent's sandbox: ${agentSandbox.name}.`
      : "The organization's default sandbox.";

  return (
    <label
      className={`flex items-center gap-1 px-2 py-1 text-muted-foreground sm:gap-1.5 sm:px-2.5 sm:py-1.5 ${
        bare ? "focus-within:bg-elevated" : "rounded-lg bg-surface border border-border focus-within:border-primary/60"
      }`}
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
        <option value="">{agentSandbox ? `Agent's: ${agentSandbox.name}` : "Default"}</option>
        {sandboxes.map((box) => (
          <option key={box.id} value={box.id}>
            {box.team_id ? `${box.name} · ${teamName(box) ?? "team"}` : box.name}
          </option>
        ))}
      </select>
    </label>
  );
};
