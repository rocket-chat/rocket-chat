"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import { KeyRound, Trash2, AlertCircle, CheckCircle2 } from "lucide-react";

interface StoredKey {
  provider: string;
  fingerprint: string;
  api_base: string | null;
  owner: "user" | "org";
  created_at: number;
}

/** A key the deployment holds in its environment: shown, never revealed, and replaceable. */
interface PlatformKey {
  provider: string;
  env_var: string;
  fingerprint: string;
}

const COMMON_PROVIDERS = ["openrouter", "anthropic", "openai", "gemini", "deepseek", "ollama"];

/**
 * Bring-your-own provider keys. A member's key is used for their own calls; an
 * organization key is the fallback for everyone (subject to the organization's BYOK policy), and
 * replaces the deployment's own key, set in the environment, for that provider. The secret is
 * write-only: the server only ever returns a fingerprint.
 */
export function ProviderKeysPanel({ mode }: { mode: "user" | "org" }) {
  const base = mode === "user" ? "/v1/user/model-keys" : "/v1/admin/model-keys";
  const [keys, setKeys] = useState<StoredKey[]>([]);
  const [platformKeys, setPlatformKeys] = useState<PlatformKey[]>([]);
  const apiKeyInput = useRef<HTMLInputElement>(null);
  const [provider, setProvider] = useState("openrouter");
  const [apiKey, setApiKey] = useState("");
  const [apiBase, setApiBase] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await fetch(base);
      if (res.ok) setKeys(await res.json());
      if (mode === "org") {
        const platform = await fetch("/v1/admin/model-keys/platform");
        if (platform.ok) setPlatformKeys(await platform.json());
      }
    } catch {
      /* the panel simply stays empty */
    }
  }, [base, mode]);

  useEffect(() => {
    load();
  }, [load]);

  const save = async () => {
    setSaving(true);
    setError(null);
    setNotice(null);
    try {
      const res = await fetch(`${base}/${encodeURIComponent(provider.trim().toLowerCase())}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ api_key: apiKey, api_base: apiBase.trim() || null }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(typeof body.detail === "string" ? body.detail : `HTTP ${res.status}`);
      }
      setApiKey("");
      setApiBase("");
      setNotice(`${provider} key saved`);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save the key");
    } finally {
      setSaving(false);
    }
  };

  const remove = async (name: string) => {
    await fetch(`${base}/${encodeURIComponent(name)}`, { method: "DELETE" });
    setNotice(`${name} key removed`);
    await load();
  };

  const override = (name: string) => {
    setProvider(name);
    apiKeyInput.current?.focus();
  };

  // One row per provider: the organization's own key where there is one, else the deployment's.
  const providers = Array.from(new Set([...keys.map((k) => k.provider), ...platformKeys.map((k) => k.provider)])).sort();

  return (
    <section className="bg-surface-card rounded-xl border border-surface-border p-6 space-y-4" data-testid={`provider-keys-${mode}`}>
      <div>
        <h2 className="text-sm font-semibold text-foreground flex items-center gap-2">
          <KeyRound className="w-4 h-4 text-brand" />
          {mode === "user" ? "Your provider keys" : "Organization provider keys"}
        </h2>
        <p className="text-xs text-neutral-500 dark:text-neutral-400 mt-1">
          {mode === "user"
            ? "Calls you make use your own key when one is stored for the model's provider; that spend is not charged to the organization budget."
            : "Used by every member without a key of their own. A key set in the deployment's environment is listed here; save a key for the same provider to use it for this organization instead. Set a base URL to reach a private vLLM or Ollama server."}
        </p>
      </div>

      {error && (
        <div role="alert" className="flex items-center gap-2 p-3 rounded-lg bg-rose-950/60 border border-rose-800/60 text-rose-400 text-xs">
          <AlertCircle className="w-4 h-4 shrink-0" />
          <span>{error}</span>
        </div>
      )}
      {notice && (
        <div className="flex items-center gap-2 p-3 rounded-lg bg-emerald-950/60 border border-emerald-800/60 text-emerald-400 text-xs">
          <CheckCircle2 className="w-4 h-4 shrink-0" />
          <span>{notice}</span>
        </div>
      )}

      <ul className="divide-y divide-surface-border">
        {providers.length === 0 && <li className="py-2 text-xs text-neutral-500">No keys stored.</li>}
        {providers.map((name) => {
          const own = keys.find((k) => k.provider === name);
          const deployment = platformKeys.find((k) => k.provider === name);
          return (
            <li key={name} data-testid={`provider-key-${name}`} className="py-2 flex items-center justify-between gap-3 text-xs">
              <span className="min-w-0">
                <strong>{name}</strong>{" "}
                {own ? (
                  <>
                    <span className="text-neutral-500 dark:text-neutral-400">
                      {mode === "org" ? "Organization key" : "Your key"}
                    </span>{" "}
                    {own.fingerprint}
                    {own.api_base ? ` - ${own.api_base}` : ""}
                    {deployment && (
                      <span className="block text-neutral-500 dark:text-neutral-400">
                        Replaces the deployment&apos;s key ({deployment.env_var}) for this organization.
                      </span>
                    )}
                  </>
                ) : (
                  deployment && (
                    <>
                      <span className="text-neutral-500 dark:text-neutral-400">Deployment key</span> {deployment.fingerprint}
                      <span className="block text-neutral-500 dark:text-neutral-400">
                        Set by the environment variable {deployment.env_var}.
                      </span>
                    </>
                  )
                )}
              </span>
              {own ? (
                <button
                  type="button"
                  aria-label={`Remove ${name} key`}
                  onClick={() => remove(name)}
                  className="text-rose-400 hover:text-rose-300"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              ) : (
                <button
                  type="button"
                  aria-label={`Override the ${name} key for this organization`}
                  onClick={() => override(name)}
                  className="shrink-0 rounded-lg border border-surface-border px-2 py-1 text-[11px] text-foreground hover:border-brand/50"
                >
                  Override
                </button>
              )}
            </li>
          );
        })}
      </ul>

      <div className="grid gap-2 md:grid-cols-[10rem_1fr_1fr_auto]">
        <input
          list="provider-names"
          aria-label="Provider"
          value={provider}
          onChange={(e) => setProvider(e.target.value)}
          className="px-3 py-1.5 rounded-lg bg-surface-subnav border border-surface-border text-xs"
        />
        <datalist id="provider-names">
          {COMMON_PROVIDERS.map((name) => (
            <option key={name} value={name} />
          ))}
        </datalist>
        <input
          ref={apiKeyInput}
          type="password"
          autoComplete="off"
          aria-label="API key"
          placeholder="API key"
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
          className="px-3 py-1.5 rounded-lg bg-surface-subnav border border-surface-border text-xs"
        />
        <input
          aria-label="Base URL (optional)"
          placeholder="Base URL (optional)"
          value={apiBase}
          onChange={(e) => setApiBase(e.target.value)}
          className="px-3 py-1.5 rounded-lg bg-surface-subnav border border-surface-border text-xs"
        />
        <button
          type="button"
          onClick={save}
          disabled={saving || apiKey.length < 8 || !provider.trim()}
          className="px-3 py-1.5 rounded-lg bg-brand text-white text-xs font-semibold disabled:opacity-50"
        >
          Save key
        </button>
      </div>
    </section>
  );
}
