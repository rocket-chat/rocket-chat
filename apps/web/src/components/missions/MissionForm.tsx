"use client";

import React, { useEffect, useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { Button } from "../ui/Button";
import { Field, Input, Select, Textarea } from "../ui/Field";
import { OwnerSelect, useMoveConfirmation } from "../settings/Owner";
import { useAccess } from "../../lib/access";
import { useTeams } from "../../lib/teams";
import { SUPERVISION_OPTIONS } from "../../lib/supervision";
import { creatableOwners, ownerFields, ownerKey, type OwnerKey } from "../../lib/ownership";
import {
  DELIVERY_OPTIONS,
  MAX_TRIGGERS,
  MODEL_TIERS,
  OWN_SOURCES,
  SCHEDULES,
  TRIGGER_SOURCES,
  blankDelivery,
  blankTrigger,
  completeMission,
  missionProblem,
  parseOutputSchema,
  type DeliveryKind,
  type MissionBody,
  type MissionDelivery,
  type MissionPreset,
  type MissionTrigger,
  type SavedMission,
} from "../../lib/missions";

interface Named {
  id: string;
  name: string;
  scope?: string;
  team_id?: string | null;
}

const SOURCE_GROUPS = Array.from(new Set(TRIGGER_SOURCES.map((s) => s.group)));

const list = (value: string) =>
  value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);

/** The choices the form offers: agents, sandbox types and the organization's Slack bots. */
function useChoices(): { agents: Named[]; sandboxes: Named[]; bots: Named[] } {
  const [choices, setChoices] = useState<{ agents: Named[]; sandboxes: Named[]; bots: Named[] }>({ agents: [], sandboxes: [], bots: [] });
  useEffect(() => {
    let cancelled = false;
    const json = async <T,>(url: string, fallback: T): Promise<T> => {
      try {
        const res = await fetch(url);
        return res.ok ? ((await res.json()) as T) : fallback;
      } catch {
        return fallback;
      }
    };
    void (async () => {
      const [agents, sandboxes, bots] = await Promise.all([
        json<Named[]>("/v1/agents", []),
        json<{ sandboxes?: Named[] }>("/v1/sandboxes", {}),
        json<Named[]>("/v1/missions/chat-bots", []),
      ]);
      if (!cancelled) {
        setChoices({ agents: Array.isArray(agents) ? agents : [], sandboxes: sandboxes.sandboxes ?? [], bots: Array.isArray(bots) ? bots : [] });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);
  return choices;
}

const TriggerFields: React.FC<{
  index: number;
  trigger: MissionTrigger;
  onChange: (trigger: MissionTrigger) => void;
  onRemove: () => void;
}> = ({ index, trigger, onChange, onRemove }) => {
  const own = OWN_SOURCES.has(trigger.source);
  const setFilter = (key: keyof MissionTrigger["filter"], value: string) =>
    onChange({ ...trigger, filter: { ...trigger.filter, [key]: list(value) } });
  return (
    <li className="space-y-3 rounded-lg border border-surface-border p-3" data-testid={`mission-trigger-${index}`}>
      <div className="flex items-end gap-2">
        <Field label={`Trigger ${index + 1}`} className="flex-1">
          {(control) => (
            <Select
              {...control}
              data-testid={`mission-trigger-source-${index}`}
              value={trigger.source}
              onChange={(e) => onChange({ ...blankTrigger(e.target.value), filter: trigger.filter })}
            >
              {SOURCE_GROUPS.map((group) => (
                <optgroup key={group} label={group}>
                  {TRIGGER_SOURCES.filter((s) => s.group === group).map((source) => (
                    <option key={source.value} value={source.value}>
                      {source.label}
                    </option>
                  ))}
                </optgroup>
              ))}
            </Select>
          )}
        </Field>
        <Button size="md" variant="ghost" aria-label={`Remove trigger ${index + 1}`} onClick={onRemove}>
          <Trash2 className="h-4 w-4" aria-hidden="true" />
        </Button>
      </div>
      {trigger.source === "schedule" && (
        <div className="grid gap-3 sm:grid-cols-3">
          <Field label="Schedule (cron)" hint={SCHEDULES.map((s) => `${s.cron}: ${s.label.toLowerCase()}`).join(" · ")} className="sm:col-span-2">
            {(control) => (
              <Input
                {...control}
                data-testid={`mission-trigger-cron-${index}`}
                value={trigger.schedule}
                list="mission-schedules"
                onChange={(e) => onChange({ ...trigger, schedule: e.target.value })}
              />
            )}
          </Field>
          <Field label="Time zone">
            {(control) => <Input {...control} value={trigger.timezone} onChange={(e) => onChange({ ...trigger, timezone: e.target.value })} />}
          </Field>
        </div>
      )}
      {trigger.source === "webhook" && (
        <p className="text-xs text-neutral-600 dark:text-neutral-400">
          The mission gets a URL of its own and a secret to sign calls with; both are shown once it is saved.
        </p>
      )}
      {!own && (
        <div className="grid gap-3 sm:grid-cols-3">
          <Field label="Repositories" hint="owner/name, globs allowed; empty: any">
            {(control) => (
              <Input {...control} placeholder="acme/*" value={trigger.filter.repositories.join(", ")} onChange={(e) => setFilter("repositories", e.target.value)} />
            )}
          </Field>
          <Field label="Labels" hint="Any of these; empty: any">
            {(control) => <Input {...control} value={trigger.filter.labels.join(", ")} onChange={(e) => setFilter("labels", e.target.value)} />}
          </Field>
          <Field label="Branches" hint="Globs; empty: any">
            {(control) => <Input {...control} value={trigger.filter.branches.join(", ")} onChange={(e) => setFilter("branches", e.target.value)} />}
          </Field>
        </div>
      )}
    </li>
  );
};

const DeliveryFields: React.FC<{
  delivery: MissionDelivery;
  schemaText: string;
  bots: Named[];
  onChange: (delivery: MissionDelivery) => void;
  onSchemaText: (text: string) => void;
  onRemove: () => void;
}> = ({ delivery, schemaText, bots, onChange, onSchemaText, onRemove }) => {
  const label = DELIVERY_OPTIONS.find((d) => d.kind === delivery.kind)?.label ?? delivery.kind;
  const schema = delivery.kind === "webhook" ? parseOutputSchema(schemaText) : null;
  return (
    <li className="space-y-3 rounded-lg border border-surface-border p-3" data-testid={`mission-delivery-${delivery.kind}`}>
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-medium text-foreground">{label}</span>
        <Button size="sm" variant="ghost" aria-label={`Remove ${label}`} onClick={onRemove}>
          <Trash2 className="h-4 w-4" aria-hidden="true" />
        </Button>
      </div>
      {delivery.kind === "chat" && (
        <div className="grid gap-3 sm:grid-cols-2">
          {bots.length > 0 && (
            <Field label="Slack bot">
              {(control) => (
                <Select {...control} value={delivery.bot_id} onChange={(e) => onChange({ ...delivery, bot_id: e.target.value })}>
                  <option value="">The organization&apos;s bot</option>
                  {bots.map((bot) => (
                    <option key={bot.id} value={bot.id}>
                      {bot.name}
                    </option>
                  ))}
                </Select>
              )}
            </Field>
          )}
          <Field label="Channel id" hint="C…, G… or D…">
            {(control) => (
              <Input {...control} data-testid="mission-chat-channel" value={delivery.channel} onChange={(e) => onChange({ ...delivery, channel: e.target.value.trim() })} />
            )}
          </Field>
        </div>
      )}
      {delivery.kind === "webhook" && (
        <div className="space-y-3">
          <Field label="URL" hint="An https URL; each call is signed with a secret shown once">
            {(control) => (
              <Input {...control} data-testid="mission-webhook-url" placeholder="https://" value={delivery.url} onChange={(e) => onChange({ ...delivery, url: e.target.value })} />
            )}
          </Field>
          <Field
            label="Result schema (JSON Schema, optional)"
            hint="The object the agent must deliver; empty: a summary."
            error={schema && "error" in schema ? schema.error : null}
          >
            {(control) => (
              <Textarea
                {...control}
                data-testid="mission-webhook-schema"
                rows={5}
                className="font-mono text-xs"
                placeholder='{"type": "object", "properties": {"severity": {"enum": ["low", "high"]}}, "required": ["severity"]}'
                value={schemaText}
                onChange={(e) => onSchemaText(e.target.value)}
              />
            )}
          </Field>
        </div>
      )}
      {(delivery.kind === "github_review" || delivery.kind === "github_comment") && (
        <p className="text-xs text-neutral-600 dark:text-neutral-400">Posted on the pull request or issue the event is about, with the mission&apos;s own access.</p>
      )}
    </li>
  );
};

interface MissionFormProps {
  /** The mission edited (a stored one) or the starting point of a new one. */
  initial: MissionBody;
  editingId?: string;
  presets: MissionPreset[];
  onSaved: (saved: SavedMission) => void;
  onCancel: () => void;
}

/** One form for a mission: when it runs, who runs it, what it does, and where its result goes. */
export const MissionForm: React.FC<MissionFormProps> = ({ initial, editingId, presets, onSaved, onCancel }) => {
  const access = useAccess();
  const { teams } = useTeams();
  const choices = useChoices();
  const [confirmMove, moveDialog] = useMoveConfirmation();
  const owners = creatableOwners(access, teams, true);
  const storedOwner = editingId ? ownerKey(initial) : null;
  const [draft, setDraft] = useState<MissionBody>(initial);
  const [owner, setOwner] = useState<OwnerKey>(ownerKey(initial));
  const [schemas, setSchemas] = useState<Record<number, string>>(() =>
    Object.fromEntries(
      initial.deliveries.map((d, i) => [i, d.kind === "webhook" && Object.keys(d.output_schema).length ? JSON.stringify(d.output_schema, null, 2) : ""]),
    ),
  );
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const patch = (changes: Partial<MissionBody>) => setDraft((current) => ({ ...current, ...changes }));

  const applyPreset = (id: string) => {
    const preset = presets.find((p) => p.id === id);
    if (!preset) return;
    const next = completeMission(preset.mission);
    setDraft({ ...next, enabled: true });
    setSchemas({});
  };

  const setTrigger = (index: number, trigger: MissionTrigger) =>
    patch({ triggers: draft.triggers.map((t, i) => (i === index ? trigger : t)) });
  const setDelivery = (index: number, delivery: MissionDelivery) =>
    patch({ deliveries: draft.deliveries.map((d, i) => (i === index ? delivery : d)) });
  const removeDelivery = (index: number) => {
    patch({ deliveries: draft.deliveries.filter((_, i) => i !== index) });
    setSchemas((current) => Object.fromEntries(Object.entries(current).filter(([i]) => Number(i) !== index).map(([i, text]) => [Number(i) > index ? Number(i) - 1 : Number(i), text])));
  };
  const unusedDeliveries = DELIVERY_OPTIONS.filter((option) => !draft.deliveries.some((d) => d.kind === option.kind));
  const ownerTeam = owner.startsWith("team:") ? owner.slice(5) : null;
  const sandboxes = choices.sandboxes.filter((box) => !box.team_id || box.team_id === ownerTeam);

  const save = async (event: React.FormEvent) => {
    event.preventDefault();
    setError(null);
    const deliveries: MissionDelivery[] = [];
    for (const [index, delivery] of draft.deliveries.entries()) {
      if (delivery.kind !== "webhook") {
        deliveries.push(delivery);
        continue;
      }
      const parsed = parseOutputSchema(schemas[index] ?? "");
      if ("error" in parsed) return setError(parsed.error);
      deliveries.push({ ...delivery, output_schema: parsed.schema });
    }
    const body: MissionBody = { ...draft, deliveries, ...ownerFields(owner) };
    const problem = missionProblem(body);
    if (problem) return setError(problem);
    if (editingId && storedOwner && !(await confirmMove(body.name, storedOwner, owner, { kind: "mission", id: editingId }))) return;
    setBusy(true);
    try {
      const res = await fetch(editingId ? `/v1/missions/${encodeURIComponent(editingId)}` : "/v1/missions", {
        method: editingId ? "PUT" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const saved = (await res.json().catch(() => ({}))) as SavedMission & { detail?: unknown };
      if (!res.ok) return setError(typeof saved.detail === "string" ? saved.detail : "The mission was not saved.");
      onSaved(saved);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={(e) => void save(e)} data-testid="mission-form" className="space-y-6">
      {moveDialog}
      <datalist id="mission-schedules">
        {SCHEDULES.map((s) => (
          <option key={s.cron} value={s.cron}>
            {s.label}
          </option>
        ))}
      </datalist>
      <div className="grid gap-4 sm:grid-cols-2">
        {!editingId && presets.length > 0 && (
          <Field label="Start from a preset" hint="Fills the form; change anything before saving.">
            {(control) => (
              <Select {...control} data-testid="mission-preset" defaultValue="" onChange={(e) => applyPreset(e.target.value)}>
                <option value="">Blank mission</option>
                {presets.map((preset) => (
                  <option key={preset.id} value={preset.id}>
                    {preset.name}
                  </option>
                ))}
              </Select>
            )}
          </Field>
        )}
        <OwnerSelect testId="mission-owner" value={owner} options={owners} onChange={setOwner} />
        <Field label="Name">
          {(control) => <Input {...control} data-testid="mission-name" required value={draft.name} onChange={(e) => patch({ name: e.target.value })} />}
        </Field>
      </div>

      <fieldset className="space-y-3">
        <legend className="text-sm font-semibold text-foreground">When</legend>
        {draft.triggers.length === 0 && (
          <p className="text-xs text-neutral-600 dark:text-neutral-400">No trigger: it runs when someone presses Run now.</p>
        )}
        <ul className="space-y-3">
          {draft.triggers.map((trigger, index) => (
            <TriggerFields
              key={index}
              index={index}
              trigger={trigger}
              onChange={(next) => setTrigger(index, next)}
              onRemove={() => patch({ triggers: draft.triggers.filter((_, i) => i !== index) })}
            />
          ))}
        </ul>
        <Button
          size="sm"
          data-testid="mission-trigger-add"
          disabled={draft.triggers.length >= MAX_TRIGGERS}
          onClick={() => patch({ triggers: [...draft.triggers, blankTrigger("github_pr")] })}
        >
          <Plus className="h-3.5 w-3.5" aria-hidden="true" /> Add a trigger
        </Button>
      </fieldset>

      <fieldset className="space-y-3">
        <legend className="text-sm font-semibold text-foreground">Who</legend>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <Field label="Agent">
            {(control) => (
              <Select {...control} data-testid="mission-agent" value={draft.agent_id} onChange={(e) => patch({ agent_id: e.target.value })}>
                <option value="">{draft.mode === "review" ? "The reviewer" : "The default agent"}</option>
                {choices.agents.map((agent) => (
                  <option key={agent.id} value={agent.id}>
                    {agent.name}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          <Field label="Mode" hint="A review posts one review on the pull request and changes no code.">
            {(control) => (
              <Select {...control} data-testid="mission-mode" value={draft.mode} onChange={(e) => patch({ mode: e.target.value === "review" ? "review" : "work" })}>
                <option value="work">Do the work</option>
                <option value="review">Review the pull request</option>
              </Select>
            )}
          </Field>
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
          <label className="flex items-center gap-2 self-end pb-2 text-sm text-foreground">
            <input type="checkbox" checked={draft.auto_fix} onChange={(e) => patch({ auto_fix: e.target.checked })} />
            Follow up its pull request (failed checks, review comments)
          </label>
        </div>
      </fieldset>

      <fieldset className="space-y-3">
        <legend className="text-sm font-semibold text-foreground">What</legend>
        <Field label="Instructions" hint="Given to the agent before what the event says.">
          {(control) => (
            <Textarea {...control} data-testid="mission-instructions" rows={4} value={draft.instructions} onChange={(e) => patch({ instructions: e.target.value })} />
          )}
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Repository (optional)" hint="owner/name; an event's own repository wins">
            {(control) => <Input {...control} placeholder="owner/name" value={draft.git_repo} onChange={(e) => patch({ git_repo: e.target.value })} />}
          </Field>
          <Field label="Branch (optional)">
            {(control) => <Input {...control} value={draft.git_branch} onChange={(e) => patch({ git_branch: e.target.value })} />}
          </Field>
        </div>
      </fieldset>

      <fieldset className="space-y-3">
        <legend className="text-sm font-semibold text-foreground">Deliver to</legend>
        <p className="text-xs text-neutral-600 dark:text-neutral-400">Every run is a session you can open. The platform also posts the result to:</p>
        <ul className="space-y-3">
          {draft.deliveries.map((delivery, index) => (
            <DeliveryFields
              key={delivery.kind}
              delivery={delivery}
              schemaText={schemas[index] ?? ""}
              bots={choices.bots}
              onChange={(next) => setDelivery(index, next)}
              onSchemaText={(text) => setSchemas((current) => ({ ...current, [index]: text }))}
              onRemove={() => removeDelivery(index)}
            />
          ))}
        </ul>
        {unusedDeliveries.length > 0 && (
          <Field label="Add a delivery" className="max-w-xs">
            {(control) => (
              <Select
                {...control}
                data-testid="mission-delivery-add"
                value=""
                onChange={(e) => e.target.value && patch({ deliveries: [...draft.deliveries, blankDelivery(e.target.value as DeliveryKind)] })}
              >
                <option value="">Choose…</option>
                {unusedDeliveries.map((option) => (
                  <option key={option.kind} value={option.kind}>
                    {option.label}
                  </option>
                ))}
              </Select>
            )}
          </Field>
        )}
      </fieldset>

      {error && (
        <p role="alert" data-testid="mission-error" className="text-sm text-rose-600 dark:text-rose-400">
          {error}
        </p>
      )}
      <div className="flex gap-2">
        <Button type="submit" variant="primary" data-testid="mission-save" disabled={busy}>
          {editingId ? "Save mission" : "Create mission"}
        </Button>
        <Button variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  );
};
