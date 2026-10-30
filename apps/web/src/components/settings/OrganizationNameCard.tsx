"use client";

import React, { useEffect, useState } from "react";
import { Building2 } from "lucide-react";
import { Field, Input } from "../ui/Field";
import { refreshAccess, useAccess } from "../../lib/access";

/** The organization's name, which an administrator can change (its id never changes). */
export const OrganizationNameCard: React.FC = () => {
  const access = useAccess();
  const [saved, setSaved] = useState("");
  const [name, setName] = useState("");
  const [status, setStatus] = useState<{ error: boolean; text: string } | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetch("/v1/organization")
      .then((res) => (res.ok ? res.json() : null))
      .then((body: { name?: string } | null) => {
        if (cancelled || !body?.name) return;
        setSaved(body.name);
        setName(body.name);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  const trimmed = name.trim();
  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!trimmed || trimmed === saved) return;
    setSaving(true);
    setStatus(null);
    try {
      const res = await fetch("/v1/organization", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: trimmed }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        setStatus({ error: true, text: typeof body.detail === "string" ? body.detail : "Could not rename the organization." });
        return;
      }
      setSaved(body.name);
      setName(body.name);
      setStatus({ error: false, text: "Renamed." });
      // The switcher and headers show the name from the caller's access.
      await refreshAccess();
    } finally {
      setSaving(false);
    }
  };

  return (
    <form onSubmit={save} data-testid="organization-name" className="rounded-xl border border-surface-border bg-surface-card p-5">
      <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold text-foreground">
        <Building2 className="h-4 w-4 text-brand" aria-hidden="true" /> Organization
      </h2>
      <div className="flex flex-wrap items-end gap-3">
        <Field label="Name" hint={access.isAdmin ? "Shown to every member. Links and settings keep working." : "Only an administrator can rename it."}>
          {(control) => (
            <Input
              {...control}
              data-testid="organization-name-input"
              value={name}
              maxLength={255}
              disabled={!access.isAdmin || saving}
              onChange={(e) => setName(e.target.value)}
              className="max-w-sm"
            />
          )}
        </Field>
        {access.isAdmin && (
          <button
            type="submit"
            data-testid="organization-name-save"
            disabled={saving || !trimmed || trimmed === saved}
            className="rounded-lg bg-brand px-3 py-2 text-xs font-semibold text-white hover:bg-brand-hover disabled:opacity-50"
          >
            Rename
          </button>
        )}
      </div>
      {status && (
        <p role={status.error ? "alert" : "status"} className={`mt-2 text-xs ${status.error ? "text-rose-500" : "text-emerald-700 dark:text-emerald-400"}`}>
          {status.text}
        </p>
      )}
    </form>
  );
};
