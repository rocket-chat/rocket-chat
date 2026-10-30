"use client";

import React, { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";
import { refreshAccess } from "../../lib/access";

interface Joined {
  id: string;
  name: string;
  role: string;
}

function AcceptInvite() {
  const token = useSearchParams().get("token") ?? "";
  const [joined, setJoined] = useState<Joined | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const accept = async () => {
    setBusy(true);
    setError(null);
    const res = await fetch("/v1/invites/accept", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    });
    const body = await res.json().catch(() => ({}));
    setBusy(false);
    if (res.status === 401) {
      window.location.assign(`/login?callbackUrl=${encodeURIComponent(window.location.href)}`);
      return;
    }
    if (!res.ok) {
      setError(typeof body.detail === "string" ? body.detail : "This invitation could not be used.");
      return;
    }
    setJoined(body as Joined);
  };

  const open = async () => {
    if (!joined) return;
    const res = await fetch("/v1/auth/switch-org", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ org_id: joined.id }),
    });
    if (res.ok) {
      await refreshAccess();
      window.location.assign("/chat/draft");
    }
  };

  return (
    <main className="flex min-h-full items-center justify-center bg-surface-base p-6">
      <div className="w-full max-w-md space-y-4 rounded-xl border border-surface-border bg-surface-card p-6">
        <h1 className="font-display text-lg font-semibold text-foreground">Join an organization</h1>
        {!token && <p className="text-sm text-neutral-600 dark:text-neutral-400">This link has no invitation in it.</p>}
        {token && !joined && (
          <>
            <p className="text-sm text-neutral-600 dark:text-neutral-400">
              You were invited to an organization on Rocket Chat. Accepting adds it to your organizations.
            </p>
            <button
              type="button"
              data-testid="invite-accept"
              disabled={busy}
              onClick={() => void accept()}
              className="rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white hover:bg-brand-hover disabled:opacity-50"
            >
              {busy ? "Joining…" : "Accept the invitation"}
            </button>
          </>
        )}
        {joined && (
          <>
            <p role="status" className="text-sm text-foreground">
              You joined {joined.name} as {joined.role}.
            </p>
            <button
              type="button"
              onClick={() => void open()}
              className="rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white hover:bg-brand-hover"
            >
              Open {joined.name}
            </button>
          </>
        )}
        {error && (
          <p role="alert" className="text-sm text-rose-500">
            {error}
          </p>
        )}
      </div>
    </main>
  );
}

export default function InvitePage() {
  return (
    <Suspense fallback={null}>
      <AcceptInvite />
    </Suspense>
  );
}
