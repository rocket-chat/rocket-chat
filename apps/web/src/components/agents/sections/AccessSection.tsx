"use client";

import React, { useCallback, useEffect, useState } from "react";
import { Field, Select } from "../../ui/Field";
import type { SectionProps } from "./types";

interface Grant {
  principal_kind: "everyone" | "member" | "role" | "guests";
  principal_id: string;
}

type Mode = "everyone" | "only";

/** Who may use an organization agent: everyone (the default), or only some people and roles,
 * and whether guests (Slack, guest tokens) may. Saved on its own, at once. */
export const AccessSection: React.FC<SectionProps> = ({ draft, isAdmin }) => {
  const [grants, setGrants] = useState<Grant[]>([]);
  const [mode, setMode] = useState<Mode>("everyone");
  const [guests, setGuests] = useState(false);
  const [people, setPeople] = useState("");
  const [roles, setRoles] = useState<string[]>([]);
  const [status, setStatus] = useState<string | null>(null);

  const load = useCallback(async () => {
    const res = await fetch(`/v1/agents/${encodeURIComponent(draft.id)}/access`);
    if (!res.ok) return;
    const body = (await res.json()) as { grants: Grant[] };
    const list = body.grants ?? [];
    setGrants(list);
    const named = list.filter((g) => g.principal_kind === "member" || g.principal_kind === "role");
    setMode(list.length === 0 || list.some((g) => g.principal_kind === "everyone") || named.length === 0 ? "everyone" : "only");
    setGuests(list.some((g) => g.principal_kind === "guests"));
    setPeople(list.filter((g) => g.principal_kind === "member").map((g) => g.principal_id).join("\n"));
    setRoles(list.filter((g) => g.principal_kind === "role").map((g) => g.principal_id));
  }, [draft.id]);

  useEffect(() => {
    void load();
  }, [load]);

  const save = async () => {
    const next: Grant[] = [];
    if (mode === "everyone" && guests) next.push({ principal_kind: "everyone", principal_id: "" });
    if (mode === "only") {
      people
        .split(/[\n,]/)
        .map((p) => p.trim())
        .filter(Boolean)
        .forEach((id) => next.push({ principal_kind: "member", principal_id: id }));
      roles.forEach((role) => next.push({ principal_kind: "role", principal_id: role }));
    }
    if (guests) next.push({ principal_kind: "guests", principal_id: "" });
    const res = await fetch(`/v1/agents/${encodeURIComponent(draft.id)}/access`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ grants: next }),
    });
    const body = await res.json().catch(() => ({}));
    setStatus(res.ok ? "Access saved." : typeof body.detail === "string" ? body.detail : "Access was not saved.");
    if (res.ok) await load();
  };

  if (!isAdmin) return <p className="text-xs text-neutral-500">Administrators decide who may use this agent.</p>;

  return (
    <div className="space-y-4" data-testid="agent-access">
      <fieldset className="space-y-2">
        <legend className="text-xs font-semibold text-foreground">Who may use it</legend>
        <label className="flex items-center gap-2 text-xs">
          <input type="radio" name="access-mode" checked={mode === "everyone"} onChange={() => setMode("everyone")} />
          Every member (default)
        </label>
        <label className="flex items-center gap-2 text-xs">
          <input type="radio" name="access-mode" data-testid="agent-access-only" checked={mode === "only"} onChange={() => setMode("only")} />
          Only these people and roles (administrators always may)
        </label>
      </fieldset>
      {mode === "only" && (
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="People (member ids, one per line)">
            {(control) => (
              <textarea
                {...control}
                rows={3}
                value={people}
                data-testid="agent-access-people"
                onChange={(e) => setPeople(e.target.value)}
                className="w-full rounded-lg border border-border bg-surface-subnav px-3 py-2 text-xs"
              />
            )}
          </Field>
          <Field label="Roles">
            {(control) => (
              <Select
                {...control}
                multiple
                value={roles}
                onChange={(e) => setRoles(Array.from(e.target.selectedOptions).map((o) => o.value))}
              >
                <option value="member">member</option>
                <option value="viewer">viewer</option>
              </Select>
            )}
          </Field>
        </div>
      )}
      <label className="flex items-center gap-2 text-xs text-foreground">
        <input type="checkbox" data-testid="agent-access-guests" checked={guests} onChange={(e) => setGuests(e.target.checked)} />
        Guests too (Slack users who have not paired, guest API tokens)
      </label>
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={() => void save()}
          data-testid="agent-access-save"
          className="rounded-lg bg-brand px-3 py-2 text-xs font-semibold text-white hover:bg-brand-hover"
        >
          Save access
        </button>
        {status && (
          <span role="status" className="text-xs text-neutral-500">
            {status}
          </span>
        )}
        <span className="text-[11px] text-neutral-500">{grants.length === 0 ? "No grants: every member." : `${grants.length} grant(s).`}</span>
      </div>
    </div>
  );
};
