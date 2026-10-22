"use client";

import React, { useCallback, useEffect, useState } from "react";
import { Hammer, Trash2 } from "lucide-react";
import { Button } from "../ui/Button";
import { Card, SectionTitle } from "../ui/Card";
import { Field, Input, Textarea } from "../ui/Field";
import { IconButton } from "../ui/IconButton";

interface RepositorySetup {
  id: string;
  repository: string;
  folder: string;
  command: string;
  timeout_seconds: number;
  enabled: boolean;
}

const EMPTY: RepositorySetup = { id: "", repository: "", folder: "", command: "", timeout_seconds: 900, enabled: true };

/**
 * How the organization prepares folders of its repositories: commands the platform runs once per
 * workspace, before the agent's first command in a matching folder.
 */
export const RepositorySetupsPanel: React.FC = () => {
  const [setups, setSetups] = useState<RepositorySetup[]>([]);
  const [draft, setDraft] = useState<RepositorySetup>(EMPTY);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const res = await fetch("/v1/repository-setups");
    if (res.ok) setSetups(await res.json());
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const save = async () => {
    setError(null);
    const res = await fetch("/v1/repository-setups", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(draft),
    });
    if (!res.ok) {
      setError("Could not save the setup: check the repository and the commands.");
      return;
    }
    setDraft(EMPTY);
    await load();
  };

  const remove = async (id: string) => {
    await fetch(`/v1/repository-setups/${encodeURIComponent(id)}`, { method: "DELETE" });
    await load();
  };

  return (
    <section data-testid="repository-setups" className="space-y-4">
      <SectionTitle
        icon={<Hammer className="h-4 w-4" aria-hidden="true" />}
        title="Repository setups"
        description="Commands that prepare a folder of a repository (dependencies, code generation) before the agent builds or tests there. They run once per workspace, before the agent's first command in a matching folder, with the agent's own access; a failed setup stops that command and shows why."
      />
      {setups.length > 0 && (
        <ul className="space-y-2">
          {setups.map((setup) => (
            <li key={setup.id} data-testid="repository-setup">
              <Card className="flex items-start gap-3 p-3">
                <div className="min-w-0 flex-1">
                  <p className="text-xs font-medium text-foreground">
                    {setup.repository}
                    {setup.folder ? ` / ${setup.folder}` : " (root)"}
                    {!setup.enabled && <span className="ml-2 text-neutral-500">disabled</span>}
                  </p>
                  <pre className="mt-1 whitespace-pre-wrap font-mono text-[11px] text-neutral-600 dark:text-neutral-400">
                    {setup.command}
                  </pre>
                </div>
                <Button size="sm" onClick={() => setDraft(setup)}>
                  Edit
                </Button>
                <IconButton label={`Delete the setup of ${setup.repository}`} onClick={() => void remove(setup.id)}>
                  <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                </IconButton>
              </Card>
            </li>
          ))}
        </ul>
      )}
      <Card className="space-y-3 p-4">
        <div className="grid gap-3 md:grid-cols-2">
          <Field label="Repository" hint="owner/name, or a pattern such as acme/*">
            {(control) => (
              <Input
                {...control}
                data-testid="setup-repository"
                value={draft.repository}
                onChange={(e) => setDraft({ ...draft, repository: e.target.value })}
                placeholder="acme/backend-services"
              />
            )}
          </Field>
          <Field label="Folder" hint="A folder inside it; * for each top-level folder; empty for the root">
            {(control) => (
              <Input
                {...control}
                data-testid="setup-folder"
                value={draft.folder}
                onChange={(e) => setDraft({ ...draft, folder: e.target.value })}
                placeholder="*"
              />
            )}
          </Field>
        </div>
        <Field label="Commands" hint="Bash, run in that folder" error={error ?? undefined}>
          {(control) => (
            <Textarea
              {...control}
              data-testid="setup-command"
              rows={4}
              className="font-mono text-xs"
              value={draft.command}
              onChange={(e) => setDraft({ ...draft, command: e.target.value })}
              placeholder={"npm ci\nnpm run generate\nnpm run build"}
            />
          )}
        </Field>
        <div className="flex items-center gap-2">
          <Button
            variant="primary"
            data-testid="setup-save"
            disabled={!draft.repository.trim() || !draft.command.trim()}
            onClick={() => void save()}
          >
            {draft.id ? "Save the setup" : "Add the setup"}
          </Button>
          {draft.id && <Button onClick={() => setDraft(EMPTY)}>Cancel</Button>}
        </div>
      </Card>
    </section>
  );
};
