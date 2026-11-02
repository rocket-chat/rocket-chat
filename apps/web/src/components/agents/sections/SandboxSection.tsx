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
    </div>
  );
};
