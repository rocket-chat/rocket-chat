"use client";

import React, { useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { KeyRound, AlertCircle, CheckCircle2 } from "lucide-react";

/** Change the signed-in member's password. Only shown on installs with password sign-in. */
export function ChangePasswordPanel() {
  const forced = useSearchParams().get("change_password") === "1";
  const [available, setAvailable] = useState(false);
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    fetch("/api/auth/configured-providers")
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => setAvailable(Boolean(data?.local)))
      .catch(() => setAvailable(false));
  }, []);

  if (!available) return null;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setError(null);
    setDone(false);
    try {
      const res = await fetch("/api/auth/local/password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ current_password: current, new_password: next }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof body.detail === "string" ? body.detail : "Could not change the password");
      setCurrent("");
      setNext("");
      setDone(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not change the password");
    } finally {
      setSaving(false);
    }
  };

  return (
    <form
      onSubmit={submit}
      data-testid="change-password"
      className="bg-surface-card rounded-xl border border-surface-border p-6 space-y-4"
    >
      <div>
        <h2 className="text-sm font-semibold text-foreground flex items-center gap-2">
          <KeyRound className="w-4 h-4 text-brand" />
          Password
        </h2>
        <p className="text-xs text-neutral-500 dark:text-neutral-400 mt-1">
          {forced
            ? "An administrator issued you a temporary password. Choose your own to continue."
            : "At least 12 characters. A long passphrase is better than a short complex password."}
        </p>
      </div>
      {error && (
        <div role="alert" className="flex items-center gap-2 p-3 rounded-lg bg-rose-950/60 border border-rose-800/60 text-rose-400 text-xs font-mono">
          <AlertCircle className="w-4 h-4 shrink-0" />
          <span>{error}</span>
        </div>
      )}
      {done && (
        <div className="flex items-center gap-2 p-3 rounded-lg bg-emerald-950/60 border border-emerald-800/60 text-emerald-400 text-xs font-mono">
          <CheckCircle2 className="w-4 h-4 shrink-0" />
          <span>Password changed</span>
        </div>
      )}
      <div className="grid gap-2 md:grid-cols-2">
        <input
          type="password"
          autoComplete="current-password"
          aria-label="Current password"
          placeholder="Current password"
          value={current}
          onChange={(e) => setCurrent(e.target.value)}
          required
          className="px-3 py-1.5 rounded-lg bg-surface-subnav border border-surface-border text-xs font-mono"
        />
        <input
          type="password"
          autoComplete="new-password"
          aria-label="New password"
          placeholder="New password"
          value={next}
          onChange={(e) => setNext(e.target.value)}
          required
          minLength={12}
          className="px-3 py-1.5 rounded-lg bg-surface-subnav border border-surface-border text-xs font-mono"
        />
      </div>
      <button
        type="submit"
        disabled={saving}
        className="px-4 py-1.5 rounded-lg bg-brand text-white text-xs font-semibold disabled:opacity-50"
      >
        Change password
      </button>
    </form>
  );
}
