"use client";

import React from "react";
import { Plus, Trash2 } from "lucide-react";
import { Button } from "../../ui/Button";
import { Field, Input, Textarea } from "../../ui/Field";
import { MAX_REPOSITORIES, needsGithubWrite, type MissionBody } from "../../../lib/missions";

const Repositories: React.FC<{ repos: string[]; onChange: (repos: string[]) => void }> = ({ repos, onChange }) => {
  const rows = repos.length ? repos : [""];
  return (
    <fieldset className="space-y-2" data-testid="mission-repos">
      <legend className="text-xs font-medium text-neutral-600 dark:text-neutral-300">Repositories to clone (optional)</legend>
      <p className="text-xs text-neutral-500 dark:text-neutral-400">owner/name; an event&apos;s own repository comes first.</p>
      <ul className="space-y-2">
        {rows.map((repo, index) => (
          <li key={index} className="flex items-center gap-2">
            <Input
              aria-label={`Repository ${index + 1}`}
              data-testid={`mission-repo-${index}`}
              className="max-w-md"
              placeholder="owner/name"
              value={repo}
              onChange={(e) => onChange(rows.map((r, i) => (i === index ? e.target.value : r)))}
            />
            {rows.length > 1 && (
              <Button size="sm" variant="ghost" aria-label={`Remove repository ${index + 1}`} onClick={() => onChange(rows.filter((_, i) => i !== index))}>
                <Trash2 className="h-4 w-4" aria-hidden="true" />
              </Button>
            )}
          </li>
        ))}
      </ul>
      <Button size="sm" data-testid="mission-repo-add" disabled={rows.length >= MAX_REPOSITORIES || !rows[rows.length - 1].trim()} onClick={() => onChange([...rows, ""])}>
        <Plus className="h-3.5 w-3.5" aria-hidden="true" /> Add a repository
      </Button>
    </fieldset>
  );
};

/** What the agent is asked to do, where, and whether it may change GitHub. */
export const TaskStep: React.FC<{
  draft: MissionBody;
  advanced: boolean;
  patch: (changes: Partial<MissionBody>) => void;
}> = ({ draft, advanced, patch }) => {
  const forced = needsGithubWrite(draft);
  return (
    <div className="space-y-4">
      <Field
        label={draft.mode === "review" ? "Instructions (optional)" : "Instructions"}
        hint="Given to the agent before what the event says."
      >
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
      <Repositories repos={draft.git_repos} onChange={(git_repos) => patch({ git_repos })} />
      {advanced && (
        <Field label="Branch (optional)" hint="Checked out in the first repository" className="max-w-md">
          {(control) => <Input {...control} value={draft.git_branch} onChange={(e) => patch({ git_branch: e.target.value })} />}
        </Field>
      )}
      <div className="space-y-1">
        <label className="flex items-center gap-2 text-sm text-foreground">
          <input
            type="checkbox"
            data-testid="mission-github-write"
            checked={draft.github_write || forced}
            disabled={forced}
            aria-describedby="mission-github-write-note"
            onChange={(e) => patch({ github_write: e.target.checked })}
          />
          Allow GitHub writes (push branches, open pull requests)
        </label>
        <p id="mission-github-write-note" className="pl-6 text-xs text-neutral-600 dark:text-neutral-400">
          {forced
            ? "Required: the mission opens or follows up a pull request."
            : "Off, every run only reads GitHub, whoever it runs as."}
        </p>
      </div>
    </div>
  );
};
