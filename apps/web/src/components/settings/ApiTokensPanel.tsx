"use client";

import React, { useCallback, useEffect, useState } from "react";
import { AlertCircle, Check, Copy, Trash2 } from "lucide-react";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { Card, SectionTitle } from "../ui/Card";
import { EmptyState } from "../ui/EmptyState";
import { Field, Input, Select } from "../ui/Field";
import { IconButton } from "../ui/IconButton";

export type TokenScope = "personal" | "guest";

interface ApiToken {
  id: string;
  name: string;
  user_id: string;
  guest: boolean;
  created_at: number;
  expires_at: number;
  last_used_at: number | null;
}

interface CreatedToken extends ApiToken {
  token: string;
}

const ENDPOINT: Record<TokenScope, string> = {
  personal: "/v1/user/api-tokens",
  guest: "/v1/admin/api-tokens",
};

const LIFETIMES = [
  { days: 30, label: "30 days" },
  { days: 90, label: "90 days" },
  { days: 365, label: "1 year" },
];

const WORDING: Record<TokenScope, { empty: string; placeholder: string; noun: string }> = {
  personal: {
    empty: "Create a token to call the API from a script or another tool, acting as you.",
    placeholder: "e.g. nightly report script",
    noun: "personal",
  },
  guest: {
    empty: "Create a token for a client that has no account here, such as a bot.",
    placeholder: "e.g. support triage bot",
    noun: "guest",
  },
};

const formatDate = (unixSeconds: number): string => new Date(unixSeconds * 1000).toLocaleDateString();

async function errorOf(res: Response, fallback: string): Promise<string> {
  const body = await res.json().catch(() => ({}));
  return typeof body.detail === "string" ? body.detail : fallback;
}

/**
 * Create, list and revoke API tokens. Personal tokens belong to the signed-in member; guest tokens
 * are an administrator's, and the admin endpoint also lists every member's personal ones, which
 * this page leaves to their owners.
 */
export const ApiTokensPanel: React.FC<{ scope: TokenScope }> = ({ scope }) => {
  const [tokens, setTokens] = useState<ApiToken[] | null>(null);
  const [name, setName] = useState("");
  const [days, setDays] = useState(90);
  const [created, setCreated] = useState<CreatedToken | null>(null);
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const wording = WORDING[scope];

  const load = useCallback(async () => {
    try {
      const res = await fetch(ENDPOINT[scope]);
      if (!res.ok) throw new Error(await errorOf(res, "Could not load the tokens"));
      const all: ApiToken[] = await res.json();
      setTokens(scope === "guest" ? all.filter((t) => t.guest) : all);
    } catch (err) {
      setTokens([]);
      setError(err instanceof Error ? err.message : "Could not load the tokens");
    }
  }, [scope]);

  useEffect(() => {
    void load();
  }, [load]);

  const create = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(ENDPOINT[scope], {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name.trim(), expires_in_days: days }),
      });
      if (!res.ok) throw new Error(await errorOf(res, "Could not create the token"));
      setCreated(await res.json());
      setCopied(false);
      setName("");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create the token");
    } finally {
      setBusy(false);
    }
  };

  const revoke = async (token: ApiToken) => {
    setError(null);
    try {
      const res = await fetch(`${ENDPOINT[scope]}/${token.id}`, { method: "DELETE" });
      if (!res.ok) throw new Error(await errorOf(res, "Could not revoke the token"));
      if (created?.id === token.id) setCreated(null);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not revoke the token");
    }
  };

  const copy = async () => {
    if (!created) return;
    await navigator.clipboard.writeText(created.token);
    setCopied(true);
  };

  return (
    <Card className="space-y-5 p-5" data-testid={`api-tokens-${scope}`}>
      <SectionTitle
        title={scope === "guest" ? "Guest tokens" : "Personal tokens"}
        description={
          scope === "guest"
            ? "For a client without an account here, such as a bot. Each token acts as its own guest member: it can hold conversations and nothing else, whatever role you hold."
            : "A token acts as you, with the role you hold when it is used."
        }
      />

      {error && (
        <p role="alert" className="flex items-center gap-2 text-sm text-rose-600 dark:text-rose-400">
          <AlertCircle className="h-4 w-4 shrink-0" aria-hidden="true" />
          {error}
        </p>
      )}

      {created && (
        <div role="status" className="space-y-2 rounded-lg border border-emerald-500/30 bg-emerald-500/10 p-3">
          <p className="text-sm text-foreground">
            Copy <strong>{created.name}</strong> now: it is shown only once.
          </p>
          <div className="flex items-center gap-2">
            <code
              data-testid="new-token"
              className="min-w-0 flex-1 break-all rounded-md bg-surface-base px-2 py-1.5 font-mono text-xs text-foreground"
            >
              {created.token}
            </code>
            <Button size="sm" onClick={copy}>
              {copied ? <Check className="h-3.5 w-3.5" aria-hidden="true" /> : <Copy className="h-3.5 w-3.5" aria-hidden="true" />}
              {copied ? "Copied" : "Copy"}
            </Button>
          </div>
        </div>
      )}

      <form onSubmit={create} className="grid gap-3 sm:grid-cols-[1fr_9rem_auto] sm:items-end">
        <Field label="Name">
          {(control) => (
            <Input
              {...control}
              value={name}
              maxLength={100}
              required
              placeholder={wording.placeholder}
              onChange={(e) => setName(e.target.value)}
            />
          )}
        </Field>
        <Field label="Expires after">
          {(control) => (
            <Select {...control} value={days} onChange={(e) => setDays(Number(e.target.value))}>
              {LIFETIMES.map((lifetime) => (
                <option key={lifetime.days} value={lifetime.days}>
                  {lifetime.label}
                </option>
              ))}
            </Select>
          )}
        </Field>
        <Button type="submit" variant="primary" disabled={busy || !name.trim()}>
          Create token
        </Button>
      </form>
      <p className="-mt-2 text-xs text-neutral-500 dark:text-neutral-400">
        The name says what the token is for, so you can recognise it later.
      </p>

      {tokens !== null && tokens.length === 0 ? (
        <EmptyState title={`No ${wording.noun} tokens`} description={wording.empty} />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <caption className="sr-only">{scope === "guest" ? "Guest tokens" : "Personal tokens"}</caption>
            <thead className="text-xs text-neutral-500 dark:text-neutral-400">
              <tr>
                <th scope="col" className="py-2 pr-3 font-medium">Name</th>
                <th scope="col" className="py-2 pr-3 font-medium">Created</th>
                <th scope="col" className="py-2 pr-3 font-medium">Expires</th>
                <th scope="col" className="py-2 pr-3 font-medium">Last used</th>
                <th scope="col" className="py-2 font-medium"><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {(tokens ?? []).map((token) => (
                <tr key={token.id} className="border-t border-surface-border">
                  <td className="py-2 pr-3 text-foreground">
                    {token.name}
                    {token.expires_at * 1000 < Date.now() && <Badge tone="warning" className="ml-2">Expired</Badge>}
                  </td>
                  <td className="py-2 pr-3 text-neutral-600 dark:text-neutral-400">{formatDate(token.created_at)}</td>
                  <td className="py-2 pr-3 text-neutral-600 dark:text-neutral-400">{formatDate(token.expires_at)}</td>
                  <td className="py-2 pr-3 text-neutral-600 dark:text-neutral-400">
                    {token.last_used_at ? formatDate(token.last_used_at) : "Never"}
                  </td>
                  <td className="py-2 text-right">
                    <IconButton label={`Revoke ${token.name}`} onClick={() => revoke(token)}>
                      <Trash2 className="h-4 w-4" aria-hidden="true" />
                    </IconButton>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
};
