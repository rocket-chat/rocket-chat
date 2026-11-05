"use client";

import React from "react";
import { Eye, Hammer } from "lucide-react";
import { Field, Select } from "../../ui/Field";
import { SUPERVISION_OPTIONS } from "../../../lib/supervision";
import { MODEL_TIERS, type MissionBody } from "../../../lib/missions";
import { Tile, TileGroup } from "./Tile";

export interface Named {
  id: string;
  name: string;
  scope?: string;
  team_id?: string | null;
}

/** Who runs the mission and how: the agent, work or review, and (Advanced) its limits. */
export const AgentStep: React.FC<{
  draft: MissionBody;
  agents: Named[];
  sandboxes: Named[];
  advanced: boolean;
  patch: (changes: Partial<MissionBody>) => void;
}> = ({ draft, agents, sandboxes, advanced, patch }) => (
  <div className="space-y-4">
    <Field label="Agent">
      {(control) => (
        <Select {...control} data-testid="mission-agent" className="max-w-md" value={draft.agent_id} onChange={(e) => patch({ agent_id: e.target.value })}>
          <option value="">{draft.mode === "review" ? "The reviewer" : "The default agent"}</option>
          {agents.map((agent) => (
            <option key={agent.id} value={agent.id}>
              {agent.name}
            </option>
          ))}
        </Select>
      )}
    </Field>
    <TileGroup label="What the agent does">
      <Tile
        icon={Hammer}
        label="Do the work"
        hint="Changes code and opens a pull request when it needs one."
        selected={draft.mode === "work"}
        testId="mission-mode-work"
        onClick={() => patch({ mode: "work" })}
      />
      <Tile
        icon={Eye}
        label="Review the pull request"
        hint="Posts one review on the pull request; changes no code."
        selected={draft.mode === "review"}
        testId="mission-mode-review"
        onClick={() => patch({ mode: "review" })}
      />
    </TileGroup>
    <label className="flex items-center gap-2 text-sm text-foreground">
      <input type="checkbox" checked={draft.auto_fix} onChange={(e) => patch({ auto_fix: e.target.checked })} />
      Follow up its pull request (failed checks, review comments)
    </label>
    {advanced && (
      <div className="grid gap-4 sm:grid-cols-3" data-testid="mission-agent-advanced">
        <Field label="Supervision">
          {(control) => (
            <Select {...control} value={draft.permission_mode} onChange={(e) => patch({ permission_mode: e.target.value })}>
              <option value="">Organization default</option>
              {SUPERVISION_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </Select>
          )}
        </Field>
        <Field label="Model">
          {(control) => (
            <Select {...control} value={draft.model_tier} onChange={(e) => patch({ model_tier: e.target.value })}>
              {MODEL_TIERS.map((tier) => (
                <option key={tier.value} value={tier.value}>
                  {tier.label}
                </option>
              ))}
            </Select>
          )}
        </Field>
        <Field label="Sandbox type">
          {(control) => (
            <Select {...control} value={draft.sandbox_type} onChange={(e) => patch({ sandbox_type: e.target.value })}>
              <option value="">The agent&apos;s, else the default</option>
              {sandboxes.map((box) => (
                <option key={box.id} value={box.id}>
                  {box.name}
                </option>
              ))}
            </Select>
          )}
        </Field>
      </div>
    )}
  </div>
);
