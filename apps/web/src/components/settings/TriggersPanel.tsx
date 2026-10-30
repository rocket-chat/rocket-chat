"use client";

import React, { useCallback, useEffect, useState } from "react";
import { AlertCircle, Plus, Trash2 } from "lucide-react";
import { Field, Input, Select, Textarea } from "../ui/Field";

export interface Trigger {
  id: string;
  name: string;
  source: string;
  filter: { repositories: string[]; labels: string[]; branches: string[] };
  agent_id: string;
  permission_mode: string;
  model_tier: string;
  sandbox_type: string;
  mode: string;
  auto_fix: boolean;
  instructions: string;
  schedule: string;
  enabled: boolean;
}

interface Named {
  id: string;
  name: string;
}

export const SOURCES: { value: string; label: string }[] = [
  { value: "github_issue_label", label: "GitHub: an issue gets a label" },
  { value: "github_issue_comment", label: "GitHub: a comment addresses the agent" },
  { value: "github_pr", label: "GitHub: a pull request is opened or updated" },
  { value: "github_pr_review_comment", label: "GitHub: a review comment" },
  { value: "github_workflow_failed", label: "GitHub: a workflow run fails" },
  { value: "gitlab_issue_label", label: "GitLab: an issue gets a label" },
  { value: "gitlab_mr", label: "GitLab: a merge request is opened or updated" },
  { value: "gitlab_note", label: "GitLab: a comment addresses the agent" },
  { value: "gitlab_pipeline_failed", label: "GitLab: a pipeline fails" },
  { value: "jira_issue", label: "Jira: an issue is created or updated" },
  { value: "linear_issue", label: "Linear: an issue is created or updated" },
  { value: "schedule", label: "On a schedule (cron, UTC)" },
  { value: "api", label: "From the API (POST /v1/triggers/{id}/run)" },
];
const REVIEW_SOURCES = new Set(["github_pr", "gitlab_mr"]);

const list = (text: string): string[] =>
  text
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean);

/** The organization's triggers: which events start a session, with which agent and how. */
export const TriggersPanel: React.FC = () => {
  const [triggers, setTriggers] = useState<Trigger[]>([]);
  const [agents, setAgents] = useState<Named[]>([]);
  const [name, setName] = useState("");
  const [source, setSource] = useState("github_issue_label");
  const [repositories, setRepositories] = useState("");
  const [labels, setLabels] = useState("");
  const [agentId, setAgentId] = useState("");
  const [mode, setMode] = useState("work");
  const [autoFix, setAutoFix] = useState(false);
  const [schedule, setSchedule] = useState("");
  const [instructions, setInstructions] = useState("");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const res = await fetch("/v1/admin/triggers");
    if (res.ok) setTriggers((await res.json()).triggers ?? []);
  }, []);

  useEffect(() => {
    load();
    fetch("/v1/agents")
      .then((res) => (res.ok ? res.json() : []))
      .then((rows: Named[]) => setAgents(Array.isArray(rows) ? rows : []))
      .catch(() => setAgents([]));
  }, [load]);

  const add = async (event: React.FormEvent) => {
    event.preventDefault();
    setError(null);
    const res = await fetch("/v1/admin/triggers", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name,
        source,
        filter: { repositories: list(repositories), labels: list(labels), branches: [] },
        agent_id: agentId,
        mode: REVIEW_SOURCES.has(source) ? mode : "work",
        auto_fix: autoFix,
        schedule,
        instructions,
      }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      setError(typeof body.detail === "string" ? body.detail : "The trigger was not saved.");
      return;
    }
    setName("");
    setInstructions("");
    await load();
  };

  const toggle = async (trigger: Trigger) => {
    const { id, ...body } = trigger;
    // Shown at once; the list is read back from the server after the change.
    setTriggers((current) => current.map((t) => (t.id === id ? { ...t, enabled: !t.enabled } : t)));
    await fetch(`/v1/admin/triggers/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...body, enabled: !trigger.enabled }),
    });
    await load();
  };

  const remove = async (id: string) => {
    await fetch(`/v1/admin/triggers/${id}`, { method: "DELETE" });
    await load();
  };

  const sourceLabel = (value: string) => SOURCES.find((s) => s.value === value)?.label ?? value;

  return (
    <section className="space-y-4 rounded-lg border border-border bg-surface-card p-4" aria-labelledby="triggers-title" data-testid="triggers-panel">
      <div>
        <h2 id="triggers-title" className="text-sm font-semibold text-foreground">
          Triggers
        </h2>
        <p className="text-xs text-muted-foreground">
          Events that start (or wake) a session. Without any, issues labelled with the deployment&apos;s label
          and comments that address the agent start one.
        </p>
      </div>
      {triggers.length > 0 && (
        <ul className="divide-y divide-border rounded-lg border border-border" data-testid="triggers-list">
          {triggers.map((trigger) => (
            <li key={trigger.id} className="flex items-center justify-between gap-3 p-3 text-xs">
              <div className="min-w-0">
                <div className="font-medium text-foreground">{trigger.name}</div>
                <div className="truncate text-muted-foreground">
                  {sourceLabel(trigger.source)}
                  {trigger.mode === "review" ? " · review" : ""}
                  {trigger.auto_fix ? " · fixes CI and review comments" : ""}
                  {trigger.filter.repositories.length ? ` · ${trigger.filter.repositories.join(", ")}` : ""}
                  {trigger.filter.labels.length ? ` · labels ${trigger.filter.labels.join(", ")}` : ""}
                </div>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                <label className="flex items-center gap-1 text-muted-foreground">
                  <input type="checkbox" checked={trigger.enabled} onChange={() => toggle(trigger)} aria-label={`Enable ${trigger.name}`} />
                  On
                </label>
                <button type="button" onClick={() => remove(trigger.id)} aria-label={`Delete ${trigger.name}`} className="text-muted-foreground hover:text-rose-500">
                  <Trash2 className="h-4 w-4" aria-hidden="true" />
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
      <form onSubmit={add} className="grid gap-3 sm:grid-cols-2">
        <Field label="Name">
          {(control) => <Input {...control} required value={name} onChange={(e) => setName(e.target.value)} placeholder="Review backend pull requests" data-testid="trigger-name" />}
        </Field>
        <Field label="When">
          {(control) => (
            <Select {...control} value={source} onChange={(e) => setSource(e.target.value)} data-testid="trigger-source">
              {SOURCES.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
            </Select>
          )}
        </Field>
        <Field label="Repositories" hint="owner/name patterns, comma-separated (acme/*). Empty: any.">
          {(control) => <Input {...control} value={repositories} onChange={(e) => setRepositories(e.target.value)} />}
        </Field>
        <Field label="Labels" hint="Any of these labels. Empty: any.">
          {(control) => <Input {...control} value={labels} onChange={(e) => setLabels(e.target.value)} />}
        </Field>
        <Field label="Agent">
          {(control) => (
            <Select {...control} value={agentId} onChange={(e) => setAgentId(e.target.value)}>
              <option value="">{mode === "review" ? "The reviewer" : "The organization's default"}</option>
              {agents.map((agent) => (
                <option key={agent.id} value={agent.id}>
                  {agent.name}
                </option>
              ))}
            </Select>
          )}
        </Field>
        {REVIEW_SOURCES.has(source) && (
          <Field label="Mode">
            {(control) => (
              <Select {...control} value={mode} onChange={(e) => setMode(e.target.value)} data-testid="trigger-mode">
                <option value="work">Work on it</option>
                <option value="review">Review it (one review with inline comments)</option>
              </Select>
            )}
          </Field>
        )}
        {source === "schedule" && (
          <Field label="Schedule" hint="Cron, five fields, UTC (0 9 * * 1-5).">
            {(control) => <Input {...control} required value={schedule} onChange={(e) => setSchedule(e.target.value)} />}
          </Field>
        )}
        <Field label="Instructions" hint="Placed before the event's text." className="sm:col-span-2">
          {(control) => <Textarea {...control} rows={2} value={instructions} onChange={(e) => setInstructions(e.target.value)} />}
        </Field>
        <label className="flex items-center gap-2 text-xs text-foreground sm:col-span-2">
          <input type="checkbox" checked={autoFix} onChange={(e) => setAutoFix(e.target.checked)} />
          After its pull request, fix failed checks and clear review comments (asks in the thread when unsure)
        </label>
        {error && (
          <p role="alert" className="flex items-center gap-2 text-xs text-rose-600 dark:text-rose-400 sm:col-span-2">
            <AlertCircle className="h-4 w-4" aria-hidden="true" /> {error}
          </p>
        )}
        <div className="sm:col-span-2">
          <button type="submit" className="inline-flex items-center gap-1 rounded-lg bg-brand px-3 py-1.5 text-xs font-semibold text-white hover:bg-brand-hover" data-testid="trigger-add">
            <Plus className="h-3.5 w-3.5" aria-hidden="true" /> Add trigger
          </button>
        </div>
      </form>
    </section>
  );
};
