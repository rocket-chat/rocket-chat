"use client";

import React, { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { BookMarked, Pencil, Play, Plus, Trash2 } from "lucide-react";
import { Button } from "./ui/Button";
import { Card, SectionTitle } from "./ui/Card";
import { EmptyState } from "./ui/EmptyState";
import { Field, Input, Select, Textarea } from "./ui/Field";
import { SUPERVISION_OPTIONS } from "../lib/supervision";
import { OwnerBadge, OwnerSelect, useMoveConfirmation } from "./settings/Owner";
import { useAccess } from "../lib/access";
import { useTeams } from "../lib/teams";
import { canManageOwner, creatableOwners, ownerFields, ownerKey, type OwnerKey } from "../lib/ownership";

interface Template {
  id: string;
  name: string;
  description: string;
  prompt: string;
  scope: "org" | "team" | "user";
  team_id?: string | null;
  agent_id?: string | null;
  git_repo: string | null;
  git_branch?: string | null;
  permission_mode: string | null;
}

/** Reusable ways to start a session (playbooks): a prompt, optionally a repository and a mode. */
export const TemplatesSection: React.FC = () => {
  const router = useRouter();
  const [templates, setTemplates] = useState<Template[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState({
    name: "",
    prompt: "",
    repo: "",
    permission_mode: "auto",
  });
  const access = useAccess();
  const { teams } = useTeams();
  const owners = creatableOwners(access, teams, true);
  const [owner, setOwner] = useState<OwnerKey>("me");
  // The template the form edits (its fields and owner); null when it makes a new one.
  const [editing, setEditing] = useState<Template | null>(null);
  const [confirmMove, moveDialog] = useMoveConfirmation();

  const openNew = () => {
    setEditing(null);
    setForm({ name: "", prompt: "", repo: "", permission_mode: "auto" });
    setOwner("me");
    setShowForm((open) => !open || editing !== null);
  };

  const openEdit = (template: Template) => {
    setEditing(template);
    setForm({ name: template.name, prompt: template.prompt, repo: template.git_repo ?? "", permission_mode: template.permission_mode ?? "auto" });
    setOwner(ownerKey(template));
    setShowForm(true);
  };

  const load = useCallback(async () => {
    const res = await fetch("/v1/templates");
    if (res.ok) setTemplates(await res.json());
    setLoaded(true);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const create = async (event: React.FormEvent) => {
    event.preventDefault();
    setError(null);
    if (editing && !(await confirmMove(form.name, ownerKey(editing), owner, { kind: "template", id: editing.id }))) return;
    const res = await fetch(editing ? `/v1/templates/${encodeURIComponent(editing.id)}` : "/v1/templates", {
      method: editing ? "PUT" : "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: form.name,
        prompt: form.prompt,
        ...ownerFields(owner),
        permission_mode: form.permission_mode,
        // A replace keeps what this form does not show.
        ...(editing ? { description: editing.description, agent_id: editing.agent_id ?? null, git_branch: editing.git_branch ?? null } : {}),
        ...(form.repo.trim() ? { git_repo: form.repo.trim() } : {}),
      }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      setError(typeof body.detail === "string" ? body.detail : "Could not save the template.");
      return;
    }
    setForm({ ...form, name: "", prompt: "" });
    setEditing(null);
    setShowForm(false);
    await load();
  };

  const start = async (id: string) => {
    const res = await fetch(`/v1/templates/${id}/start`, { method: "POST" });
    if (!res.ok) return setError("Could not start a session from that template.");
    const session = await res.json();
    router.push(`/chat/${session.session_id}`);
  };

  const remove = async (id: string) => {
    const res = await fetch(`/v1/templates/${id}`, { method: "DELETE" });
    if (!res.ok) setError("You cannot delete that template.");
    await load();
  };

  return (
    <section data-testid="templates-section" className="mt-12 space-y-4">
      {moveDialog}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <SectionTitle
          icon={<BookMarked className="h-4 w-4" aria-hidden="true" />}
          title="Session templates"
          description="Start a session from a saved prompt, repository and supervision level. A team's templates are managed by its administrators, the organization's by administrators."
        />
        <Button data-testid="template-new" variant="secondary" onClick={openNew}>
          <Plus className="h-4 w-4" aria-hidden="true" />
          New template
        </Button>
      </div>

      {showForm && (
        <Card className="p-5">
          <form onSubmit={create} data-testid="template-form" className="space-y-4">
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <Field label="Name">
                {(control) => (
                  <Input
                    {...control}
                    data-testid="template-name"
                    required
                    value={form.name}
                    onChange={(e) => setForm({ ...form, name: e.target.value })}
                  />
                )}
              </Field>
              <OwnerSelect testId="template-owner" value={owner} options={owners} onChange={setOwner} />
            </div>
            <Field label="Opening prompt">
              {(control) => (
                <Textarea
                  {...control}
                  data-testid="template-prompt"
                  required
                  rows={3}
                  value={form.prompt}
                  onChange={(e) => setForm({ ...form, prompt: e.target.value })}
                />
              )}
            </Field>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <Field label="Repository (optional)">
                {(control) => (
                  <Input
                    {...control}
                    value={form.repo}
                    placeholder="owner/name"
                    onChange={(e) => setForm({ ...form, repo: e.target.value })}
                  />
                )}
              </Field>
              <Field label="Supervision">
                {(control) => (
                  <Select
                    {...control}
                    value={form.permission_mode}
                    onChange={(e) => setForm({ ...form, permission_mode: e.target.value })}
                  >
                    {SUPERVISION_OPTIONS.map((o) => (
                      <option key={o.value} value={o.value}>
                        {o.label}
                      </option>
                    ))}
                  </Select>
                )}
              </Field>
            </div>
            {error && (
              <p role="alert" className="text-sm text-rose-600 dark:text-rose-400">
                {error}
              </p>
            )}
            <div className="flex gap-2">
              <Button type="submit" variant="primary" data-testid="template-create">
                Save template
              </Button>
              <Button
                variant="ghost"
                onClick={() => {
                  setShowForm(false);
                  setEditing(null);
                }}
              >
                Cancel
              </Button>
            </div>
          </form>
        </Card>
      )}

      {!showForm && error && (
        <p role="alert" className="text-sm text-rose-600 dark:text-rose-400">
          {error}
        </p>
      )}

      {loaded && templates.length === 0 && !showForm && (
        <EmptyState
          title="No templates yet"
          description="Save the prompts you reuse, then start a configured session in one click."
        />
      )}

      <ul className="space-y-2">
        {templates.map((t) => (
          <li key={t.id} data-testid="template-item">
            <Card className="flex items-center justify-between gap-3 p-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <h3 className="truncate text-sm font-semibold text-foreground">{t.name}</h3>
                  <OwnerBadge owned={t} />
                </div>
                <p className="truncate text-sm text-neutral-600 dark:text-neutral-400">{t.prompt}</p>
              </div>
              <div className="flex shrink-0 items-center gap-1">
                <Button
                  size="sm"
                  variant="ghost"
                  data-testid="template-start"
                  aria-label={`Start a session from ${t.name}`}
                  title="Start a session"
                  onClick={() => void start(t.id)}
                >
                  <Play className="h-4 w-4" />
                </Button>
                {canManageOwner(access, teams, ownerKey(t)) && (
                  <Button size="sm" variant="ghost" data-testid="template-edit" aria-label={`Edit template ${t.name}`} title="Edit template" onClick={() => openEdit(t)}>
                    <Pencil className="h-4 w-4" />
                  </Button>
                )}
                <Button
                  size="sm"
                  variant="ghost"
                  aria-label={`Delete template ${t.name}`}
                  title="Delete template"
                  onClick={() => void remove(t.id)}
                >
                  <Trash2 className="h-4 w-4" />
                </Button>
              </div>
            </Card>
          </li>
        ))}
      </ul>
    </section>
  );
};
