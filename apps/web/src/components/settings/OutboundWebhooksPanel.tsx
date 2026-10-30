"use client";

import React, { useCallback, useEffect, useState } from "react";
import { AlertCircle, Copy, Plus, Trash2 } from "lucide-react";
import { Field, Input } from "../ui/Field";

interface Webhook {
  id: string;
  url: string;
  events: string[];
  description: string;
  enabled: boolean;
  secret_fingerprint: string;
}

const EVENTS: { value: string; label: string }[] = [
  { value: "turn.completed", label: "A turn completed" },
  { value: "turn.failed", label: "A turn failed" },
  { value: "pr.opened", label: "A pull request was opened" },
  { value: "budget.blocked", label: "A spend limit blocked a turn" },
];

/** URLs that receive signed reports of how the organization's work went. */
export const OutboundWebhooksPanel: React.FC = () => {
  const [webhooks, setWebhooks] = useState<Webhook[]>([]);
  const [url, setUrl] = useState("");
  const [events, setEvents] = useState<string[]>(["turn.completed", "turn.failed"]);
  const [secret, setSecret] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const res = await fetch("/v1/admin/webhooks");
    if (res.ok) setWebhooks((await res.json()).webhooks ?? []);
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const add = async (event: React.FormEvent) => {
    event.preventDefault();
    setError(null);
    const res = await fetch("/v1/admin/webhooks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, events }),
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      setError(typeof body.detail === "string" ? body.detail : "The webhook was not saved.");
      return;
    }
    setSecret(String(body.secret ?? ""));
    setUrl("");
    await load();
  };

  const remove = async (id: string) => {
    await fetch(`/v1/admin/webhooks/${id}`, { method: "DELETE" });
    await load();
  };

  const toggleEvent = (value: string) =>
    setEvents((current) => (current.includes(value) ? current.filter((e) => e !== value) : [...current, value]));

  return (
    <section className="space-y-4 rounded-lg border border-border bg-surface-card p-4" aria-labelledby="webhooks-title" data-testid="webhooks-panel">
      <div>
        <h2 id="webhooks-title" className="text-sm font-semibold text-foreground">
          Outbound webhooks
        </h2>
        <p className="text-xs text-muted-foreground">
          Each report is a JSON POST signed with the webhook&apos;s secret: <code>X-Rocket-Signature: sha256=HMAC(secret, timestamp + &quot;.&quot; + body)</code>{" "}
          with <code>X-Rocket-Timestamp</code>. Failed deliveries are retried for about three hours.
        </p>
      </div>
      {secret && (
        <div role="status" className="space-y-1 rounded-lg border border-emerald-500/40 bg-emerald-500/10 p-3 text-xs" data-testid="webhook-secret">
          <div className="font-medium text-foreground">Signing secret (shown once)</div>
          <div className="flex items-center gap-2">
            <code className="break-all">{secret}</code>
            <button type="button" aria-label="Copy the secret" onClick={() => navigator.clipboard?.writeText(secret)}>
              <Copy className="h-3.5 w-3.5" aria-hidden="true" />
            </button>
          </div>
        </div>
      )}
      {webhooks.length > 0 && (
        <ul className="divide-y divide-border rounded-lg border border-border">
          {webhooks.map((hook) => (
            <li key={hook.id} className="flex items-center justify-between gap-3 p-3 text-xs">
              <div className="min-w-0">
                <div className="truncate font-medium text-foreground">{hook.url}</div>
                <div className="text-muted-foreground">
                  {hook.events.join(", ")} · secret {hook.secret_fingerprint}
                </div>
              </div>
              <button type="button" onClick={() => remove(hook.id)} aria-label={`Delete the webhook to ${hook.url}`} className="text-muted-foreground hover:text-rose-500">
                <Trash2 className="h-4 w-4" aria-hidden="true" />
              </button>
            </li>
          ))}
        </ul>
      )}
      <form onSubmit={add} className="space-y-3">
        <Field label="URL" hint="https only; it must be reachable from the internet.">
          {(control) => <Input {...control} required type="url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://hooks.example.com/rocket" data-testid="webhook-url" />}
        </Field>
        <fieldset className="space-y-1">
          <legend className="text-xs font-medium text-neutral-600 dark:text-neutral-300">Events</legend>
          {EVENTS.map((option) => (
            <label key={option.value} className="flex items-center gap-2 text-xs text-foreground">
              <input type="checkbox" checked={events.includes(option.value)} onChange={() => toggleEvent(option.value)} />
              {option.label} <code className="text-muted-foreground">{option.value}</code>
            </label>
          ))}
        </fieldset>
        {error && (
          <p role="alert" className="flex items-center gap-2 text-xs text-rose-600 dark:text-rose-400">
            <AlertCircle className="h-4 w-4" aria-hidden="true" /> {error}
          </p>
        )}
        <button type="submit" disabled={!events.length} className="inline-flex items-center gap-1 rounded-lg bg-brand px-3 py-1.5 text-xs font-semibold text-white hover:bg-brand-hover disabled:opacity-50" data-testid="webhook-add">
          <Plus className="h-3.5 w-3.5" aria-hidden="true" /> Add webhook
        </button>
      </form>
    </section>
  );
};
