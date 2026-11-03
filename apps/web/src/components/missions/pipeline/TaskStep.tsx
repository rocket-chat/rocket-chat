"use client";

import React from "react";
import { Field, Input, Textarea } from "../../ui/Field";
import type { MissionBody } from "../../../lib/missions";

/** What the agent is asked to do, and (optionally) where. */
export const TaskStep: React.FC<{
  draft: MissionBody;
  advanced: boolean;
  patch: (changes: Partial<MissionBody>) => void;
}> = ({ draft, advanced, patch }) => (
  <div className="space-y-4">
    <Field label="Instructions" hint="Given to the agent before what the event says.">
      {(control) => (
        <Textarea
          {...control}
          data-testid="mission-instructions"
          rows={6}
          placeholder="For example: triage the new issues, label each one and say which need a person."
          value={draft.instructions}
          onChange={(e) => patch({ instructions: e.target.value })}
        />
      )}
    </Field>
    <div className="grid gap-4 sm:grid-cols-2">
      <Field label="Repository (optional)" hint="owner/name; an event's own repository wins">
        {(control) => <Input {...control} placeholder="owner/name" value={draft.git_repo} onChange={(e) => patch({ git_repo: e.target.value })} />}
      </Field>
      {advanced && (
        <Field label="Branch (optional)">
          {(control) => <Input {...control} value={draft.git_branch} onChange={(e) => patch({ git_branch: e.target.value })} />}
        </Field>
      )}
    </div>
  </div>
);
