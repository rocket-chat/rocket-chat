"use client";

import React, { useCallback, useEffect, useState } from "react";
import { Info, Pencil, Plus, Trash2 } from "lucide-react";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { Field } from "../ui/Field";

interface Sandbox {
  id: string;
  name: string;
  image: string;
  description: string;
  capabilities: string[];
  is_default: boolean;
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
}

const EMPTY_FORM: FormValues = { name: "", image: "", description: "", browser: false };

const toPayload = (values: FormValues) => ({
  name: values.name,
  image: values.image,
  description: values.description,
  capabilities: values.browser ? ["browser"] : [],
});

interface SandboxFormProps {
  initial: FormValues;
  browserHelp: string;
  onSubmit: (values: FormValues) => Promise<void>;
  onCancel: () => void;
}

/** The fields of one sandbox entry; used to add a new entry and to edit an existing one. */
const SandboxForm: React.FC<SandboxFormProps> = ({ initial, browserHelp, onSubmit, onCancel }) => {
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
 * The sandboxes an organization offers. Each is an image plus what it can do; ticking a
 * capability is a promise that the image has what the tool needs, so the box says what that is.
 */
export const SandboxCataloguePanel: React.FC = () => {
  const [catalogue, setCatalogue] = useState<Catalogue | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | "new" | null>(null);

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

  if (!catalogue) return <p className="text-xs text-neutral-500">Loading sandboxes…</p>;
  const browserHelp = catalogue.capabilities.browser ?? "";

  return (
    <section data-testid="sandbox-catalogue" className="space-y-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-foreground">Sandboxes members can choose</h2>
          <p className="text-xs text-neutral-500 dark:text-neutral-400 mt-1">
            Each entry is a container image. New sessions start from the default unless the member
            picks another in the composer.
          </p>
        </div>
        <Button size="sm" variant="primary" data-testid="sandbox-add" onClick={() => setEditing(editing === "new" ? null : "new")}>
          <Plus className="w-3.5 h-3.5" />
          Add sandbox
        </Button>
      </div>

      {error && (
        <p role="alert" className="text-xs text-rose-600 dark:text-rose-400">
          {error}
        </p>
      )}

      {editing === "new" && (
        <Card className="p-4">
          <SandboxForm
            initial={EMPTY_FORM}
            browserHelp={browserHelp}
            onCancel={() => setEditing(null)}
            onSubmit={async (values) => {
              if (await send("/v1/admin/sandboxes", "POST", toPayload(values))) setEditing(null);
            }}
          />
        </Card>
      )}

      <div className="space-y-2">
        {catalogue.sandboxes.map((box) => (
          <Card key={box.id} className="p-4" data-testid="sandbox-row">
            {editing === box.id ? (
              <SandboxForm
                initial={{
                  name: box.name,
                  image: box.image,
                  description: box.description,
                  browser: box.capabilities.includes("browser"),
                }}
                browserHelp={browserHelp}
                onCancel={() => setEditing(null)}
                onSubmit={async (values) => {
                  if (await send(`/v1/admin/sandboxes/${box.id}`, "PATCH", toPayload(values))) {
                    setEditing(null);
                  }
                }}
              />
            ) : (
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0 space-y-1">
                <div className="flex flex-wrap items-center gap-2">
                  <h3 className="text-sm font-semibold text-foreground">{box.name}</h3>
                  {box.is_default && <Badge tone="brand">Default</Badge>}
                  {box.capabilities.map((c) => (
                    <Badge key={c} tone="info">
                      {c}
                    </Badge>
                  ))}
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
                {!box.is_default && (
                  <>
                    <Button
                      size="sm"
                      variant="ghost"
                      data-testid="sandbox-make-default"
                      onClick={() => send(`/v1/admin/sandboxes/${box.id}`, "PATCH", { is_default: true })}
                    >
                      Make default
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      aria-label={`Remove ${box.name}`}
                      data-testid="sandbox-remove"
                      onClick={() => send(`/v1/admin/sandboxes/${box.id}`, "DELETE")}
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
