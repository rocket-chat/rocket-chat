"use client";

import React, { useCallback, useEffect, useState } from "react";
import { AlertCircle, KeyRound, RefreshCw, Share2, Star, Trash2 } from "lucide-react";
import { Field, Input, Select } from "../ui/Field";

/** A stored credential as the API shows it: a fingerprint, never the secret. */
export interface Credential {
  id: string;
  label: string;
  provider: string;
  kind: string;
  owner: "org" | "user";
  owner_user_id: string | null;
  fingerprint: string;
  api_base: string | null;
  default_for_provider: boolean;
  restricted_to_agents: boolean;
  shared_with_org_agents: string[];
  created_at: number;
}

interface PlatformKey {
  provider: string;
  env_var: string;
  fingerprint: string;
}

interface OrgAgent {
  id: string;
  name: string;
  scope?: string;
  user_id?: string | null;
}

const COMMON_PROVIDERS = ["anthropic", "openrouter", "openai", "gemini", "deepseek", "ollama"];

async function errorOf(res: Response, fallback: string): Promise<string> {
  const body = await res.json().catch(() => ({}));
  return typeof body.detail === "string" ? body.detail : fallback;
}

/**
 * Provider credentials, each with a label: the organization's (``mode="org"``), which may be
 * restricted to the agents bound to them, or the member's own (``mode="user"``), which only
 * their owner may share with organization agents. Secrets are write-only.
 */
export function CredentialsPanel({ mode }: { mode: "user" | "org" }) {
  const base = mode === "user" ? "/v1/user/credentials" : "/v1/admin/credentials";
  const [credentials, setCredentials] = useState<Credential[]>([]);
  const [shared, setShared] = useState<Credential[]>([]);
  const [platform, setPlatform] = useState<PlatformKey[]>([]);
  const [agents, setAgents] = useState<OrgAgent[]>([]);
  const [provider, setProvider] = useState("anthropic");
  const [label, setLabel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [apiBase, setApiBase] = useState("");
  const [restricted, setRestricted] = useState(false);
  const [rotating, setRotating] = useState<string | null>(null);
  const [rotateKey, setRotateKey] = useState("");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const res = await fetch(base);
    if (res.ok) {
      const body = await res.json();
      if (mode === "org") {
        setCredentials(body.credentials ?? []);
        setShared(body.shared_by_members ?? []);
      } else {
        setCredentials(body ?? []);
      }
    }
    if (mode === "org") {
      const keys = await fetch("/v1/admin/credentials/platform");
      if (keys.ok) setPlatform(await keys.json());
    } else {
      const list = await fetch("/v1/agents");
      if (list.ok) {
        const all = (await list.json()) as OrgAgent[];
        setAgents(all.filter((a) => a.scope !== "user" && !a.user_id));
      }
    }
  }, [base, mode]);

  useEffect(() => {
    void load();
  }, [load]);

  const create = async (event: React.FormEvent) => {
    event.preventDefault();
    setError(null);
    const res = await fetch(base, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        provider,
        api_key: apiKey,
        label,
        api_base: apiBase || null,
        restricted_to_agents: mode === "org" && restricted,
        default_for_provider: !(mode === "org" && restricted),
      }),
    });
    if (!res.ok) {
      setError(await errorOf(res, "The key was not saved."));
      return;
    }
    setApiKey("");
    setLabel("");
    setApiBase("");
    setRestricted(false);
    await load();
  };

  const patch = async (id: string, changes: Record<string, unknown>) => {
    setError(null);
    const res = await fetch(`${base}/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(changes),
    });
    if (!res.ok) setError(await errorOf(res, "The change was not saved."));
    await load();
  };

  const rotate = async (id: string) => {
    setError(null);
    const res = await fetch(`${base}/${id}/rotate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ api_key: rotateKey }),
    });
    if (!res.ok) setError(await errorOf(res, "The key was not rotated."));
    setRotating(null);
    setRotateKey("");
    await load();
  };

  const remove = async (id: string) => {
    await fetch(`${base}/${id}`, { method: "DELETE" });
    await load();
  };

  const toggleShare = (credential: Credential, agentId: string, on: boolean) => {
    const next = new Set(credential.shared_with_org_agents);
    if (on) next.add(agentId);
    else next.delete(agentId);
    void patch(credential.id, { shared_with_org_agents: [...next] });
  };

  return (
    <section className="space-y-4" data-testid={`credentials-panel-${mode}`}>
      <div>
        <h2 className="flex items-center gap-2 font-display text-base font-semibold text-foreground">
          <KeyRound className="h-4 w-4 text-brand" aria-hidden="true" />
          {mode === "org" ? "Organization credentials" : "My provider keys"}
        </h2>
        <p className="mt-1 text-xs text-neutral-600 dark:text-neutral-400">
          {mode === "org"
            ? "Keys the organization pays for. A restricted key serves only the agents bound to it (Agents > Model & credentials)."
            : "Keys you pay for, used for your own calls as the organization allows. Share one with an organization agent so an administrator can bind it."}
        </p>
      </div>

      {error && (
        <p role="alert" className="flex items-center gap-2 text-xs text-rose-500">
          <AlertCircle className="h-4 w-4" aria-hidden="true" /> {error}
        </p>
      )}

      <ProviderSignIn owner={mode === "org" ? "org" : "member"} onError={setError} />

      <ul className="divide-y divide-surface-border rounded-xl border border-surface-border bg-surface-card">
        {credentials.length === 0 && platform.length === 0 && (
          <li className="px-4 py-3 text-xs text-neutral-500">No keys stored.</li>
        )}
        {platform.map((key) => (
          <li key={`env-${key.provider}`} data-testid={`platform-key-${key.provider}`} className="flex flex-wrap items-center gap-2 px-4 py-2 text-xs">
            <span className="font-medium text-foreground">{key.provider}</span>
            <span className="font-mono text-neutral-500">{key.fingerprint}</span>
            <span className="text-neutral-500">Set by operator ({key.env_var})</span>
          </li>
        ))}
        {credentials.map((credential) => (
          <li key={credential.id} className="space-y-2 px-4 py-3 text-xs" data-testid={`credential-${credential.id}`}>
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium text-foreground">{credential.label}</span>
              <span className="rounded border border-surface-border px-1.5 py-0.5 text-[10px] text-neutral-500">
                {credential.provider}
              </span>
              <span className="font-mono text-neutral-500">{credential.fingerprint}</span>
              {credential.kind === "oauth" && (
                <span className="rounded border border-surface-border px-1.5 py-0.5 text-[10px] text-neutral-500">Signed in</span>
              )}
              {credential.default_for_provider && (
                <span className="inline-flex items-center gap-1 text-[10px] text-brand">
                  <Star className="h-3 w-3" aria-hidden="true" /> Default
                </span>
              )}
              {credential.restricted_to_agents && (
                <span className="text-[10px] text-amber-600 dark:text-amber-400">Bound agents only</span>
              )}
              <span className="ml-auto flex items-center gap-1">
                {!credential.default_for_provider && !credential.restricted_to_agents && (
                  <button
                    type="button"
                    onClick={() => void patch(credential.id, { default_for_provider: true })}
                    className="rounded px-1.5 py-0.5 text-[10px] text-neutral-500 hover:text-foreground"
                  >
                    Make default
                  </button>
                )}
                {mode === "org" && (
                  <button
                    type="button"
                    onClick={() =>
                      void patch(credential.id, {
                        restricted_to_agents: !credential.restricted_to_agents,
                        default_for_provider: credential.restricted_to_agents,
                      })
                    }
                    className="rounded px-1.5 py-0.5 text-[10px] text-neutral-500 hover:text-foreground"
                  >
                    {credential.restricted_to_agents ? "Use for everyone" : "Restrict to bound agents"}
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => setRotating(rotating === credential.id ? null : credential.id)}
                  aria-label={`Rotate ${credential.label}`}
                  className="p-1 text-neutral-500 hover:text-foreground"
                >
                  <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
                </button>
                <button
                  type="button"
                  onClick={() => void remove(credential.id)}
                  aria-label={`Delete ${credential.label}`}
                  className="p-1 text-neutral-500 hover:text-rose-500"
                >
                  <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                </button>
              </span>
            </div>
            {rotating === credential.id && (
              <div className="flex items-end gap-2">
                <Field label="New key" className="flex-1">
                  {(control) => (
                    <Input {...control} type="password" value={rotateKey} onChange={(e) => setRotateKey(e.target.value)} />
                  )}
                </Field>
                <button
                  type="button"
                  disabled={rotateKey.length < 8}
                  onClick={() => void rotate(credential.id)}
                  className="rounded-lg bg-brand px-3 py-2 text-xs font-semibold text-white disabled:opacity-50"
                >
                  Rotate
                </button>
              </div>
            )}
            {mode === "user" && agents.length > 0 && (
              <fieldset className="flex flex-wrap items-center gap-3 text-[11px] text-neutral-600 dark:text-neutral-400">
                <legend className="mb-1 inline-flex items-center gap-1">
                  <Share2 className="h-3 w-3" aria-hidden="true" /> Shared with organization agents
                </legend>
                {agents.map((agent) => (
                  <label key={agent.id} className="inline-flex items-center gap-1">
                    <input
                      type="checkbox"
                      checked={credential.shared_with_org_agents.includes(agent.id)}
                      onChange={(e) => toggleShare(credential, agent.id, e.target.checked)}
                    />
                    {agent.name}
                  </label>
                ))}
              </fieldset>
            )}
          </li>
        ))}
      </ul>

      {mode === "org" && shared.length > 0 && (
        <div className="space-y-1">
          <h3 className="text-xs font-semibold text-foreground">Shared by members</h3>
          <ul className="divide-y divide-surface-border rounded-xl border border-surface-border bg-surface-card text-xs">
            {shared.map((credential) => (
              <li key={credential.id} className="flex flex-wrap items-center gap-2 px-4 py-2">
                <span className="text-foreground">{credential.label}</span>
                <span className="text-neutral-500">{credential.provider}</span>
                <span className="font-mono text-neutral-500">{credential.fingerprint}</span>
                <span className="text-neutral-500">
                  for {credential.shared_with_org_agents.length} agent
                  {credential.shared_with_org_agents.length === 1 ? "" : "s"}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <form
        onSubmit={create}
        className="grid gap-3 rounded-xl border border-surface-border bg-surface-card p-4 sm:grid-cols-2"
        data-testid={`credential-form-${mode}`}
      >
        <Field label="Provider">
          {(control) => (
            <Select {...control} value={provider} onChange={(e) => setProvider(e.target.value)}>
              {COMMON_PROVIDERS.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </Select>
          )}
        </Field>
        <Field label="Label" hint="How agents and the usage page name it">
          {(control) => (
            <Input {...control} value={label} placeholder={`${provider} key`} onChange={(e) => setLabel(e.target.value)} />
          )}
        </Field>
        <Field label="API key">
          {(control) => (
            <Input
              {...control}
              type="password"
              required
              minLength={8}
              value={apiKey}
              data-testid={`credential-key-${mode}`}
              onChange={(e) => setApiKey(e.target.value)}
            />
          )}
        </Field>
        <Field label="Base URL (optional)" hint="A self-hosted or proxied endpoint">
          {(control) => <Input {...control} value={apiBase} onChange={(e) => setApiBase(e.target.value)} />}
        </Field>
        {mode === "org" && (
          <label className="flex items-center gap-2 text-xs text-foreground sm:col-span-2">
            <input type="checkbox" checked={restricted} onChange={(e) => setRestricted(e.target.checked)} />
            Only for the agents I bind it to (skipped for everything else)
          </label>
        )}
        <div className="sm:col-span-2">
          <button
            type="submit"
            data-testid={`credential-save-${mode}`}
            className="rounded-lg bg-brand px-4 py-2 text-xs font-semibold text-white hover:bg-brand-hover"
          >
            Add key
          </button>
        </div>
      </form>
    </section>
  );
}

interface SignInProvider {
  provider: string;
  label: string;
}

/** "Sign in with OpenRouter": the provider issues a key of the person's after they approve. */
function ProviderSignIn({ owner, onError }: { owner: "member" | "org"; onError: (message: string) => void }) {
  const [providers, setProviders] = useState<SignInProvider[]>([]);

  useEffect(() => {
    // Back from the provider: a failed sign-in says why (a successful one is in the list).
    const query = new URLSearchParams(window.location.search);
    if (query.get("signin") === "failed") onError(`Sign-in failed: ${query.get("reason") ?? "unknown reason"}`);
  }, [onError]);

  useEffect(() => {
    fetch("/v1/credentials/oauth/providers")
      .then((res) => (res.ok ? res.json() : []))
      .then((rows: SignInProvider[]) => setProviders(Array.isArray(rows) ? rows : []))
      .catch(() => setProviders([]));
  }, []);

  const signIn = async (provider: string) => {
    const res = await fetch(`/v1/credentials/oauth/${provider}/start?owner=${owner}`, { method: "POST" });
    const body = await res.json().catch(() => ({}));
    if (!res.ok || typeof body.authorization_url !== "string") {
      onError(typeof body.detail === "string" ? body.detail : "Signing in could not start.");
      return;
    }
    window.location.assign(body.authorization_url);
  };

  if (!providers.length) return null;
  return (
    <div className="flex flex-wrap items-center gap-2" data-testid="provider-sign-in">
      {providers.map((provider) => (
        <button
          key={provider.provider}
          type="button"
          onClick={() => signIn(provider.provider)}
          className="rounded-lg border border-surface-border px-3 py-1.5 text-xs text-foreground hover:border-brand/50"
        >
          Sign in with {provider.label}
        </button>
      ))}
      <span className="text-[11px] text-neutral-500">Instead of pasting a key. Subscriptions such as Claude Pro or ChatGPT Plus cannot be used here: their providers do not allow it.</span>
    </div>
  );
}

