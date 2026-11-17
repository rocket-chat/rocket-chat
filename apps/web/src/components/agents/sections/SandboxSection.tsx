"use client";

import React from "react";
import { Field, Select } from "../../ui/Field";
import { useTeams } from "../../../lib/teams";
import type { SectionProps } from "./types";

/** The sandbox type the agent's sessions start in: the type holds the image, CPU and memory.
 * The organization's types, and for a team's agent its own team's. */
export const SandboxSection: React.FC<SectionProps> = ({ draft, patch, canEdit, registries }) => {
  const { teams } = useTeams();
  const offered = registries.sandboxes.filter(
    (box) => !box.team_id || (draft.scope === "team" && box.team_id === draft.team_id),
  );
  const teamName = (id: string) => teams.find((t) => t.id === id)?.name ?? "team";
  const known = !draft.sandbox_id || offered.some((box) => box.id === draft.sandbox_id);
  return (
    <div className="space-y-3">
      <label className="flex items-start gap-2 text-xs text-foreground">
        <input
          type="checkbox"
          data-testid="agent-sandbox-enabled"
          disabled={!canEdit}
          checked={draft.sandbox_enabled}
          onChange={(e) => patch({ sandbox_enabled: e.target.checked })}
        />
        <span>
          Run with a sandbox. Off: no shell, git or repository checkout, and no sandbox is ever
          started; reading and writing files works on files kept with the session.
        </span>
      </label>
      <label className="flex items-start gap-2 text-xs text-foreground">
        <input
          type="checkbox"
          data-testid="agent-reads-sessions"
          disabled={!canEdit}
          checked={draft.reads_agent_sessions}
          onChange={(e) => patch({ reads_agent_sessions: e.target.checked })}
        />
        <span>
          Read its other sessions: the agent may list and read the organization&apos;s sessions it
          answered in (never direct messages or private threads). Every read is audited.
        </span>
      </label>
      <label className="flex items-start gap-2 text-xs text-foreground">
        <input
          type="checkbox"
          data-testid="agent-proposes-changes"
          disabled={!canEdit}
          checked={draft.proposes_changes}
          onChange={(e) => patch({ proposes_changes: e.target.checked })}
        />
        <span>
          Propose changes to its instructions and skills. Nothing changes until someone who may
          edit the agent or the skill approves it under Settings &gt; Proposals.
        </span>
      </label>
      {draft.sandbox_enabled && (
      <Field
        label="Sandbox type"
        hint="Where this agent's new sessions run when the person starting them picks none. Administrators define the types (Resources > Sandboxes); a team's agent may also use its team's."
      >
        {(control) => (
          <Select
            {...control}
            data-testid="agent-sandbox"
            disabled={!canEdit}
            value={draft.sandbox_id}
            onChange={(e) => patch({ sandbox_id: e.target.value })}
            className="max-w-sm"
          >
            <option value="">Organization default</option>
            {offered.map((box) => (
              <option key={box.id} value={box.id}>
                {box.name}
                {box.is_default ? " (organization default)" : ""}
                {box.team_id ? ` · ${teamName(box.team_id)}` : ""}
              </option>
            ))}
            {!known && <option value={draft.sandbox_id}>Removed sandbox type (uses the default)</option>}
          </Select>
        )}
      </Field>
      )}
    </div>
  );
};
