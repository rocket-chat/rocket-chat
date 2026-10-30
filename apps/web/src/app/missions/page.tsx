"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { CalendarClock, Pause, Play, Plus, Trash2 } from "lucide-react";
import { TemplatesSection } from "../../components/TemplatesSection";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { EmptyState } from "../../components/ui/EmptyState";
import { Field, Input, Select, Textarea } from "../../components/ui/Field";
import { PageBody, PageHeader } from "../../components/ui/PageHeader";
import { SUPERVISION_OPTIONS } from "../../lib/supervision";

interface Mission {
  id: string;
  name: string;
  prompt: string;
  cron: string;
  timezone: string;
  git_repo: string | null;
  slack_channel: string | null;
  permission_mode: string | null;
  enabled: boolean;
  next_run_at: number | null;
  last_run_at: number | null;
  last_session_id: string | null;
  last_status: string | null;
}

const SCHEDULE_PRESETS: { label: string; cron: string }[] = [
  { label: "Every weekday at 09:00", cron: "0 9 * * 1-5" },
  { label: "Every night at 03:00", cron: "0 3 * * *" },
  { label: "Every Monday at 08:00", cron: "0 8 * * 1" },
  { label: "Every hour", cron: "0 * * * *" },
];

interface MissionTemplate {
  id: string;
  name: string;
  description: string;
  prompt: string;
  cron: string;
  permission_mode: string;
}

const presetLabel = (cron: string) => SCHEDULE_PRESETS.find((p) => p.cron === cron)?.label ?? cron;

const formatTime = (epoch: number | null) =>
  epoch ? new Date(epoch * 1000).toLocaleString() : "—";

export default function MissionsPage() {
  const [missions, setMissions] = useState<Mission[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState({
    name: "",
    prompt: "",
    cron: SCHEDULE_PRESETS[0].cron,
    repo: "",
    slack_channel: "",
    permission_mode: "auto",
    // A mission started from a template is created paused: switching it on is a decision.
    enabled: true,
  });
  const [templates, setTemplates] = useState<MissionTemplate[]>([]);

  const load = useCallback(async () => {
    const res = await fetch("/v1/missions");
    if (res.ok) setMissions(await res.json());
    setLoaded(true);
  }, []);

  useEffect(() => {
    fetch("/v1/missions/templates")
      .then((res) => (res.ok ? res.json() : []))
      .then((list: MissionTemplate[]) => setTemplates(Array.isArray(list) ? list : []))
      .catch(() => undefined);
  }, []);

  const applyTemplate = (id: string) => {
    const template = templates.find((t) => t.id === id);
    if (!template) return;
    setForm({
      ...form,
      name: template.name,
      prompt: template.prompt,
      cron: template.cron,
      permission_mode: template.permission_mode,
      enabled: false,
    });
  };

  useEffect(() => {
    void load();
  }, [load]);

  const create = async (event: React.FormEvent) => {
    event.preventDefault();
    setError(null);
    const res = await fetch("/v1/missions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: form.name,
        prompt: form.prompt,
        cron: form.cron,
        permission_mode: form.permission_mode,
        enabled: form.enabled,
        ...(form.repo.trim() ? { git_repo: form.repo.trim() } : {}),
        ...(form.slack_channel.trim() ? { slack_channel: form.slack_channel.trim() } : {}),
      }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      setError(typeof body.detail === "string" ? body.detail : "Could not create the mission.");
      return;
    }
    setForm({ ...form, name: "", prompt: "", enabled: true });
    setShowForm(false);
    await load();
  };

  const act = async (id: string, method: "PATCH" | "DELETE" | "POST", path = "", body?: unknown) => {
    const res = await fetch(`/v1/missions/${id}${path}`, {
      method,
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) setError("That did not work.");
    await load();
  };

  return (
    <PageBody>
      <PageHeader
        title="Scheduled missions"
        icon={<CalendarClock className="h-5 w-5" aria-hidden="true" />}
        description="A mission runs a prompt on a schedule as a normal session you own, with your permissions, your budget and the supervision level you choose. Runs that need an approval wait for you."
        actions={
          <Button data-testid="mission-new" variant="primary" onClick={() => setShowForm((open) => !open)}>
            <Plus className="h-4 w-4" aria-hidden="true" />
            New mission
          </Button>
        }
      />

      {showForm && (
        <Card className="mb-6 p-5">
          <form onSubmit={create} data-testid="mission-form" className="space-y-4">
            {templates.length > 0 && (
              <Field label="Start from a template" hint="Templates are created paused; switch the mission on when it looks right.">
                {(control) => (
                  <Select
                    {...control}
                    data-testid="mission-template"
                    defaultValue=""
                    onChange={(e) => applyTemplate(e.target.value)}
                    className="max-w-md"
                  >
                    <option value="">None</option>
                    {templates.map((template) => (
                      <option key={template.id} value={template.id}>
                        {template.name}: {template.description}
                      </option>
                    ))}
                  </Select>
                )}
              </Field>
            )}
            <Field label="Name">
              {(control) => (
                <Input
                  {...control}
                  data-testid="mission-name"
                  required
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                />
              )}
            </Field>
            <Field label="Prompt" hint="What the agent should do on every run.">
              {(control) => (
                <Textarea
                  {...control}
                  data-testid="mission-prompt"
                  required
                  rows={3}
                  value={form.prompt}
                  onChange={(e) => setForm({ ...form, prompt: e.target.value })}
                />
              )}
            </Field>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
              <Field label="Schedule">
                {(control) => (
                  <Select
                    {...control}
                    data-testid="mission-cron"
                    value={form.cron}
                    onChange={(e) => setForm({ ...form, cron: e.target.value })}
                  >
                    {SCHEDULE_PRESETS.map((p) => (
                      <option key={p.cron} value={p.cron}>
                        {p.label}
                      </option>
                    ))}
                  </Select>
                )}
              </Field>
              <Field label="Repository (optional)">
                {(control) => (
                  <Input
                    {...control}
                    data-testid="mission-repo"
                    value={form.repo}
                    placeholder="owner/name"
                    onChange={(e) => setForm({ ...form, repo: e.target.value })}
                  />
                )}
              </Field>
              <Field
                label="Report to Slack channel (optional)"
                hint="The channel id (it starts with C, G or D): in Slack, open the channel details and copy it from the bottom. The result of each run is posted there."
              >
                {(control) => (
                  <Input
                    {...control}
                    data-testid="mission-slack-channel"
                    value={form.slack_channel}
                    placeholder="C0123ABCD"
                    onChange={(e) => setForm({ ...form, slack_channel: e.target.value })}
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
              <Button type="submit" variant="primary" data-testid="mission-create">
                Schedule mission
              </Button>
              <Button variant="ghost" onClick={() => setShowForm(false)}>
                Cancel
              </Button>
            </div>
          </form>
        </Card>
      )}

      {!showForm && error && (
        <p role="alert" className="mb-4 text-sm text-rose-600 dark:text-rose-400">
          {error}
        </p>
      )}

      {loaded && missions.length === 0 && !showForm && (
        <EmptyState
          icon={<CalendarClock className="h-6 w-6" aria-hidden="true" />}
          title="No missions yet"
          description="Schedule a recurring task such as a nightly issue triage or a weekly dependency check."
          action={
            <Button variant="secondary" onClick={() => setShowForm(true)}>
              Schedule your first mission
            </Button>
          }
        />
      )}

      <ul className="space-y-3">
        {missions.map((m) => (
          <li key={m.id} data-testid="mission-item">
            <Card className="space-y-2 p-4">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <h3 className="truncate text-sm font-semibold text-foreground">{m.name}</h3>
                    {!m.enabled && <Badge tone="warning">Paused</Badge>}
                  </div>
                  <p className="text-xs text-neutral-600 dark:text-neutral-400">
                    {presetLabel(m.cron)} · {m.timezone}
                    {m.git_repo ? ` · ${m.git_repo}` : ""}
                    {m.slack_channel ? ` · reports to ${m.slack_channel}` : ""}
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-1">
                  <Button
                    size="sm"
                    variant="ghost"
                    data-testid="mission-run"
                    aria-label={`Run ${m.name} now`}
                    title="Run now"
                    onClick={() => void act(m.id, "POST", "/run")}
                  >
                    <Play className="h-4 w-4" />
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    data-testid="mission-toggle"
                    aria-label={m.enabled ? `Pause ${m.name}` : `Resume ${m.name}`}
                    title={m.enabled ? "Pause" : "Resume"}
                    onClick={() => void act(m.id, "PATCH", "", { enabled: !m.enabled })}
                  >
                    {m.enabled ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
                    <span className="sr-only">{m.enabled ? "Pause" : "Resume"}</span>
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    aria-label={`Delete ${m.name}`}
                    title="Delete"
                    onClick={() => void act(m.id, "DELETE")}
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
              </div>
              <p className="line-clamp-2 text-sm text-neutral-700 dark:text-neutral-300">{m.prompt}</p>
              <p className="text-xs text-neutral-500 dark:text-neutral-400">
                Next: {m.enabled ? formatTime(m.next_run_at) : "paused"} · Last: {formatTime(m.last_run_at)}
                {m.last_session_id && (
                  <>
                    {" · "}
                    <Link href={`/chat/${m.last_session_id}`} className="text-brand hover:underline">
                      open last run
                    </Link>
                  </>
                )}
              </p>
            </Card>
          </li>
        ))}
      </ul>

      <TemplatesSection />
    </PageBody>
  );
}
