"use client";

import React, { useCallback, useEffect, useState } from "react";
import { Info, Pencil, Plus, Trash2 } from "lucide-react";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { Field } from "../ui/Field";
import { SandboxEnvEditor, envPayload, rowsOf, type SandboxEnvRow, type SandboxEnvVar } from "./SandboxEnvEditor";
import { OwnerBadge, useMoveConfirmation } from "./Owner";
import { useAccess } from "../../lib/access";
import { useTeams } from "../../lib/teams";
import { creatableOwners, ownerFields, type OwnerKey } from "../../lib/ownership";

interface Sandbox {
  id: string;
  name: string;
  image: string;
  description: string;
  capabilities: string[];
  is_default: boolean;
  profile: string;
  cpu_limit: string;
  memory_limit: string;
  disk_size: string;
  release_after_days: number | null;
  /** Only returned to those who manage the entry. */
  env?: SandboxEnvVar[];
  /** A team's entry: its members' sessions and its agents may use it. */
  team_id?: string | null;
}

interface Profile {
  name: string;
  description: string;
}

interface Catalogue {
  sandboxes: Sandbox[];
  capabilities: Record<string, string>;
}

const inputClass =
  "w-full px-3 py-2 rounded-lg bg-surface-subnav border border-surface-border text-xs text-foreground focus:outline-none focus:border-brand";

interface FormValues {
  name: string;
  image: string;
  description: string;
  browser: boolean;
  profile: string;
  cpu: string;
  memory: string;
  disk: string;
  /** Empty inherits the organization's delay. */
  releaseDays: string;
  env: SandboxEnvRow[];
}

const EMPTY_FORM: FormValues = {
  name: "",
  image: "",
  description: "",
  browser: false,
  profile: "",
  cpu: "",
  memory: "",
  disk: "",
  releaseDays: "",
  env: [],
};

const formOf = (box: Sandbox): FormValues => ({
  name: box.name,
  image: box.image,
  description: box.description,
  browser: box.capabilities.includes("browser"),
  profile: box.profile || "",
  cpu: box.cpu_limit || "",
  memory: box.memory_limit || "",
  disk: box.disk_size || "",
  releaseDays: box.release_after_days === null || box.release_after_days === undefined ? "" : String(box.release_after_days),
  env: rowsOf(box.env),
});

const toPayload = (values: FormValues) => ({
  name: values.name,
  image: values.image,
  description: values.description,
  capabilities: values.browser ? ["browser"] : [],
  profile: values.profile,
  cpu_limit: values.cpu.trim(),
  memory_limit: values.memory.trim(),
  disk_size: values.disk.trim(),
  release_after_days: values.releaseDays.trim() === "" ? null : Number(values.releaseDays),
  env: envPayload(values.env),
});

interface SandboxFormProps {
  initial: FormValues;
  browserHelp: string;
  profiles: Profile[];
  /** Placement profiles are the operator's: only organization administrators choose one. */
  canChooseProfile: boolean;
  /** The organization's sandbox sizes, which a team's entry may not exceed (shown as a hint). */
  sizingCeiling?: Record<"cpu" | "memory" | "disk", string>;
  onSubmit: (values: FormValues) => Promise<void>;
  onCancel: () => void;
}

/** The fields of one sandbox entry; used to add a new entry and to edit an existing one. */
const SandboxForm: React.FC<SandboxFormProps> = ({ initial, browserHelp, profiles, canChooseProfile, sizingCeiling, onSubmit, onCancel }) => {
  const [form, setForm] = useState(initial);
  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    void onSubmit(form);
  };
  return (
    <form onSubmit={submit} className="grid gap-3 md:grid-cols-2">
      <Field label="Name">
        {(control) => (
          <input
            {...control}
            data-testid="sandbox-name"
            className={inputClass}
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
          />
        )}
      </Field>
      <Field label="Image" hint="For example registry.example.com/team/box:1">
        {(control) => (
          <input
            {...control}
            data-testid="sandbox-image"
            className={inputClass}
            value={form.image}
            onChange={(e) => setForm({ ...form, image: e.target.value })}
          />
        )}
      </Field>
      <div className="md:col-span-2">
        <Field label="Description (optional)">
          {(control) => (
            <input
              {...control}
              className={inputClass}
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
            />
          )}
        </Field>
      </div>
      <label className="md:col-span-2 flex items-start gap-2 text-xs text-foreground">
        <input
          type="checkbox"
          data-testid="sandbox-browser"
          className="mt-0.5"
          checked={form.browser}
          onChange={(e) => setForm({ ...form, browser: e.target.checked })}
        />
        <span>
          Browser tools
          <span
            data-testid="sandbox-browser-tooltip"
            title={browserHelp}
            className="ml-1.5 inline-flex align-middle text-neutral-500"
          >
            <Info className="w-3.5 h-3.5" aria-label={browserHelp} />
          </span>
          <span className="block text-neutral-500 dark:text-neutral-400">{browserHelp}</span>
        </span>
      </label>
      <fieldset className="md:col-span-2 grid gap-3 md:grid-cols-2 rounded-lg border border-surface-border p-3">
        <legend className="px-1 text-xs font-medium text-foreground">Runtime</legend>
        {canChooseProfile && (
        <Field
          label="Placement profile"
          hint={
            profiles.length
              ? "Node pool and sizing defined by your platform operator."
              : "None defined: your platform operator adds them (Helm sandbox.profiles)."
          }
        >
          {(control) => (
            <select
              {...control}
              data-testid="sandbox-profile"
              className={inputClass}
              value={form.profile}
              onChange={(e) => setForm({ ...form, profile: e.target.value })}
            >
              <option value="">Default placement</option>
              {profiles.map((p) => (
                <option key={p.name} value={p.name}>
                  {p.description ? `${p.name} — ${p.description}` : p.name}
                </option>
              ))}
            </select>
          )}
        </Field>
        )}
        <Field label="Release the workspace after (days)" hint="Idle days before the volume is deleted; 0 keeps it. Empty: organization default.">
          {(control) => (
            <input
              {...control}
              type="number"
              min={0}
              max={365}
              data-testid="sandbox-release-days"
              className={inputClass}
              placeholder="Organization default"
              value={form.releaseDays}
              onChange={(e) => setForm({ ...form, releaseDays: e.target.value })}
            />
          )}
        </Field>
        {(
          [
            ["cpu", "CPU cores", "e.g. 4"],
            ["memory", "Memory", "e.g. 8Gi"],
            ["disk", "Workspace disk", "e.g. 50Gi"],
          ] as const
        ).map(([key, label, example]) => (
          <Field
            key={key}
            label={label}
            hint={
              sizingCeiling
                ? `${example}. At most ${sizingCeiling[key] || "the organization's size"} (the organization's sandbox settings). Empty: organization default.`
                : `${example}. Empty: organization default; an agent's own wins.`
            }
          >
            {(control) => (
              <input
                {...control}
                data-testid={`sandbox-${key}`}
                className={inputClass}
                placeholder="Organization default"
                value={form[key]}
                onChange={(e) => setForm({ ...form, [key]: e.target.value })}
              />
            )}
          </Field>
        ))}
      </fieldset>
      <SandboxEnvEditor rows={form.env} onChange={(env) => setForm({ ...form, env })} />
      <div className="md:col-span-2 flex justify-end gap-2">
        <Button size="sm" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
        <Button size="sm" variant="primary" type="submit" data-testid="sandbox-save">
          Save
        </Button>
      </div>
    </form>
  );
};

/**
 * The sandboxes an organization offers (no ``team``), or a team's own (``team``: its members'
 * sessions and its agents may pick them). Each is an image plus what it can do; ticking a
 * capability is a promise that the image has what the tool needs, so the box says what that is.
 */
export const SandboxCataloguePanel: React.FC<{ team?: { id: string; name: string } }> = ({ team }) => {
  const access = useAccess();
  const { isAdmin } = access;
  const { teams } = useTeams();
  const [, moveDialog, chooseOwner] = useMoveConfirmation();
  // A sandbox type belongs to a team or the organization.
  const moveOptions = creatableOwners(access, teams, false);
  const [catalogue, setCatalogue] = useState<Catalogue | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | "new" | null>(null);
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [ceiling, setCeiling] = useState<Record<"cpu" | "memory" | "disk", string> | undefined>(undefined);
  const base = team ? `/v1/teams/${encodeURIComponent(team.id)}/sandboxes` : "/v1/admin/sandboxes";

  useEffect(() => {
    if (!isAdmin) return;
    fetch("/v1/admin/sandboxes/profiles")
      .then((res) => (res.ok ? res.json() : []))
      .then(setProfiles)
      .catch(() => setProfiles([]));
  }, [isAdmin]);

  // A team's entry may not be bigger than the organization's sandbox sizes.
  useEffect(() => {
    if (!team) return;
    fetch("/v1/settings/sandboxes")
      .then((res) => (res.ok ? res.json() : { effective: {} }))
      .then((body: { effective?: Record<string, unknown> }) => {
        const value = (key: string) => (body.effective?.[key] ? String(body.effective[key]) : "");
        setCeiling({ cpu: value("cpu_limit"), memory: value("memory_limit"), disk: value("disk_size") });
      })
      .catch(() => setCeiling(undefined));
  }, [team]);

  const load = useCallback(async () => {
    const res = await fetch("/v1/sandboxes");
    if (res.ok) setCatalogue(await res.json());
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const send = async (url: string, method: string, body?: unknown) => {
    setError(null);
    const res = await fetch(url, {
      method,
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) {
      const detail = (await res.json().catch(() => ({}))).detail;
      setError(typeof detail === "string" ? detail : "That did not work.");
      return false;
    }
    await load();
    return true;
  };

  const move = async (box: Sandbox) => {
    const from: OwnerKey = box.team_id ? `team:${box.team_id}` : "org";
    const to = await chooseOwner(box.name, from, moveOptions, { kind: "sandbox", id: box.id });
    if (!to) return;
    setError(null);
    const res = await fetch(`/v1/sandboxes/${encodeURIComponent(box.id)}/move`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(ownerFields(to)),
    });
    if (!res.ok) {
      const detail = (await res.json().catch(() => ({}))).detail;
      setError(typeof detail === "string" ? detail : typeof detail?.reason === "string" ? detail.reason : "The sandbox type was not moved.");
    }
    await load();
  };

  if (!catalogue) return <p className="text-xs text-neutral-500">Loading sandboxes…</p>;
  const browserHelp = catalogue.capabilities.browser ?? "";
  const entries = catalogue.sandboxes.filter((box) => (team ? box.team_id === team.id : !box.team_id));
  const formProps = { browserHelp, profiles, canChooseProfile: isAdmin, sizingCeiling: ceiling };

  return (
    <section data-testid={team ? `sandbox-catalogue-team-${team.id}` : "sandbox-catalogue"} className="space-y-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-foreground">{team ? `Team sandboxes · ${team.name}` : "Sandboxes members can choose"}</h2>
          <p className="text-xs text-neutral-600 dark:text-neutral-400 mt-1">
            {team
              ? "Images only the team's members and agents may start; the organization's default stays the default."
              : "Each entry is a container image. New sessions start from the default unless the member picks another in the composer."}
          </p>
        </div>
        <Button size="sm" variant="primary" data-testid={team ? "team-sandbox-add" : "sandbox-add"} onClick={() => setEditing(editing === "new" ? null : "new")}>
          <Plus className="w-3.5 h-3.5" />
          Add sandbox
        </Button>
      </div>

      {moveDialog}
      {error && (
        <p role="alert" className="text-xs text-rose-600 dark:text-rose-400">
          {error}
        </p>
      )}

      {editing === "new" && (
        <Card className="p-4">
          <SandboxForm
            initial={EMPTY_FORM}
            {...formProps}
            onCancel={() => setEditing(null)}
            onSubmit={async (values) => {
              if (await send(base, "POST", toPayload(values))) setEditing(null);
            }}
          />
        </Card>
      )}

      <div className="space-y-2">
        {entries.length === 0 && <p className="text-xs text-neutral-600 dark:text-neutral-400">No sandbox yet.</p>}
        {entries.map((box) => (
          <Card key={box.id} className="p-4" data-testid={team ? "team-sandbox-row" : "sandbox-row"}>
            {editing === box.id ? (
              <SandboxForm
                initial={formOf(box)}
                {...formProps}
                onCancel={() => setEditing(null)}
                onSubmit={async (values) => {
                  if (await send(`${base}/${box.id}`, "PATCH", toPayload(values))) {
                    setEditing(null);
                  }
                }}
              />
            ) : (
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0 space-y-1">
                <div className="flex flex-wrap items-center gap-2">
                  <h3 className="text-sm font-semibold text-foreground">{box.name}</h3>
                  {team && <OwnerBadge owned={{ scope: "team", team_id: box.team_id ?? null }} />}
                  {box.is_default && <Badge tone="brand">Default</Badge>}
                  {box.capabilities.map((c) => (
                    <Badge key={c} tone="info">
                      {c}
                    </Badge>
                  ))}
                  {box.profile && <Badge tone="info">profile: {box.profile}</Badge>}
                  {[box.cpu_limit && `${box.cpu_limit} CPU`, box.memory_limit, box.disk_size && `${box.disk_size} disk`]
                    .filter(Boolean)
                    .map((text) => (
                      <Badge key={String(text)}>{text}</Badge>
                    ))}
                  {box.release_after_days !== null && box.release_after_days !== undefined && (
                    <Badge>
                      {box.release_after_days === 0 ? "workspace kept" : `released after ${box.release_after_days}d`}
                    </Badge>
                  )}
                  {box.env && box.env.length > 0 && (
                    <Badge data-testid="sandbox-env-count" title={box.env.map((v) => v.name).join(", ")}>
                      {box.env.length} variable{box.env.length === 1 ? "" : "s"}
                    </Badge>
                  )}
                </div>
                <code className="block truncate text-[11px] text-neutral-500">{box.image}</code>
                {box.description && (
                  <p className="text-xs text-neutral-600 dark:text-neutral-400">{box.description}</p>
                )}
              </div>
              <div className="flex shrink-0 items-center gap-1">
                <Button
                  size="sm"
                  variant="ghost"
                  aria-label={`Edit ${box.name}`}
                  data-testid="sandbox-edit"
                  onClick={() => setEditing(box.id)}
                >
                  <Pencil className="w-3.5 h-3.5" />
                  Edit
                </Button>
                {!box.is_default && moveOptions.length > 1 && (
                  <Button size="sm" variant="ghost" data-testid="sandbox-move" onClick={() => void move(box)}>
                    Move…
                  </Button>
                )}
                {!box.is_default && (
                  <>
                    {/* The organization's default is the organization's: a team's entry is never it. */}
                    {!team && (
                    <Button
                      size="sm"
                      variant="ghost"
                      data-testid="sandbox-make-default"
                      onClick={() => send(`/v1/admin/sandboxes/${box.id}`, "PATCH", { is_default: true })}
                    >
                      Make default
                    </Button>
                    )}
                    <Button
                      size="sm"
                      variant="ghost"
                      aria-label={`Remove ${box.name}`}
                      data-testid="sandbox-remove"
                      onClick={() => send(`${base}/${box.id}`, "DELETE")}
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </Button>
                  </>
                )}
              </div>
            </div>
            )}
          </Card>
        ))}
      </div>
    </section>
  );
};
