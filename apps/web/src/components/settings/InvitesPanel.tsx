"use client";

import React, { useCallback, useEffect, useState } from "react";
import { Copy, MailPlus, Trash2 } from "lucide-react";
import { Field, Input, Select } from "../ui/Field";

interface Invite {
  id: string;
  email: string;
  role: string;
  expires_at: number;
  created_at: number;
  accepted_at: number | null;
}

const INVITE_ROLES = ["member", "viewer", "admin", "owner"] as const;

function inviteLink(token: string): string {
  return `${window.location.origin}/invite?token=${encodeURIComponent(token)}`;
}

/** Invite people by e-mail; each link works once and expires. */
export const InvitesPanel: React.FC = () => {
  const [invites, setInvites] = useState<Invite[]>([]);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<string>("member");
  const [days, setDays] = useState(7);
  const [link, setLink] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const res = await fetch("/v1/admin/invites");
    if (res.ok) setInvites(((await res.json()) as { invites: Invite[] }).invites ?? []);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const create = async (event: React.FormEvent) => {
    event.preventDefault();
    setError(null);
    setLink(null);
    const res = await fetch("/v1/admin/invites", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, role, lifetime_days: days }),
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      setError(typeof body.detail === "string" ? body.detail : "Could not create the invitation.");
      return;
    }
    setLink(inviteLink(body.token));
    setEmail("");
    await load();
  };

  const revoke = async (id: string) => {
    await fetch(`/v1/admin/invites/${id}`, { method: "DELETE" });
    await load();
  };

  return (
    <section className="space-y-3" data-testid="invites-panel">
      <h2 className="font-display text-base font-semibold text-foreground">Invite people</h2>
      <form onSubmit={create} className="grid gap-3 rounded-xl border border-surface-border bg-surface-card p-4 sm:grid-cols-[1fr_10rem_8rem_auto] sm:items-end">
        <Field label="E-mail">
          {(control) => (
            <Input
              {...control}
              type="email"
              required
              value={email}
              data-testid="invite-email"
              onChange={(e) => setEmail(e.target.value)}
            />
          )}
        </Field>
        <Field label="Role">
          {(control) => (
            <Select {...control} value={role} data-testid="invite-role" onChange={(e) => setRole(e.target.value)}>
              {INVITE_ROLES.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </Select>
          )}
        </Field>
        <Field label="Expires after (days)">
          {(control) => (
            <Input
              {...control}
              type="number"
              min={1}
              max={30}
              value={days}
              onChange={(e) => setDays(Number(e.target.value) || 7)}
            />
          )}
        </Field>
        <button
          type="submit"
          data-testid="invite-create"
          className="inline-flex items-center justify-center gap-1.5 rounded-lg bg-brand px-3 py-2 text-xs font-semibold text-white hover:bg-brand-hover"
        >
          <MailPlus className="h-3.5 w-3.5" aria-hidden="true" /> Create link
        </button>
      </form>
      {error && (
        <p role="alert" className="text-xs text-rose-500">
          {error}
        </p>
      )}
      {link && (
        <div className="flex items-center gap-2 rounded-lg border border-brand/30 bg-brand/5 p-3 text-xs" data-testid="invite-link">
          <span className="min-w-0 flex-1 truncate font-mono text-foreground">{link}</span>
          <button
            type="button"
            onClick={() => void navigator.clipboard?.writeText(link)}
            className="inline-flex items-center gap-1 text-brand hover:underline"
          >
            <Copy className="h-3.5 w-3.5" aria-hidden="true" /> Copy
          </button>
          <span className="text-neutral-500">Shown once: send it to the person.</span>
        </div>
      )}
      {invites.length > 0 && (
        <ul className="divide-y divide-surface-border rounded-xl border border-surface-border bg-surface-card text-xs">
          {invites.map((invite) => (
            <li key={invite.id} className="flex items-center justify-between gap-2 px-4 py-2">
              <span className="text-foreground">{invite.email}</span>
              <span className="text-neutral-500">
                {invite.role} ·{" "}
                {invite.accepted_at
                  ? "accepted"
                  : invite.expires_at * 1000 < Date.now()
                    ? "expired"
                    : `expires ${new Date(invite.expires_at * 1000).toLocaleDateString()}`}
              </span>
              {!invite.accepted_at && (
                <button
                  type="button"
                  onClick={() => void revoke(invite.id)}
                  aria-label={`Withdraw the invitation for ${invite.email}`}
                  className="p-1 text-neutral-500 hover:text-rose-500"
                >
                  <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
};
