"use client";

import React from "react";
import { Field, Select } from "../../ui/Field";
import type { SectionProps } from "./types";

/** The sandbox type the agent's sessions start in: the type holds the image, CPU and memory. */
export const SandboxSection: React.FC<SectionProps> = ({ draft, patch, canEdit, registries }) => {
  const known = !draft.sandbox_id || registries.sandboxes.some((box) => box.id === draft.sandbox_id);
  return (
    <div className="space-y-3">
      <Field
        label="Sandbox type"
        hint="Where this agent's new sessions run when the person starting them picks none. Administrators define the types (Organization > Sandboxes)."
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
            {registries.sandboxes.map((box) => (
              <option key={box.id} value={box.id}>
                {box.name}
                {box.is_default ? " (organization default)" : ""}
              </option>
            ))}
            {!known && <option value={draft.sandbox_id}>Removed sandbox type (uses the default)</option>}
          </Select>
        )}
      </Field>
    </div>
  );
};
