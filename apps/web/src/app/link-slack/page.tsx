"use client";

import React, { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";

interface Preview {
  slack_user_id: string;
  team_id: string;
}

// Only a conversation of this platform is opened afterwards: the link is typed by Slack, not trusted.
const SESSION_ID = /^[\w.:-]{1,128}$/;

function LinkSlackAccount() {
  const params = useSearchParams();
  const code = params.get("code") ?? "";
  const session = params.get("session") ?? "";
  const next = SESSION_ID.test(session) ? `/chat/${encodeURIComponent(session)}` : "/settings/slack";
  const [preview, setPreview] = useState<Preview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!code) return;
    void fetch(`/v1/slack/pair/preview?code=${encodeURIComponent(code)}`).then(async (res) => {
      const body = await res.json().catch(() => ({}));
      if (res.ok) setPreview(body as Preview);
      else setError(typeof body.detail === "string" ? body.detail : "This link could not be used.");
    });
  }, [code]);

  const confirm = async () => {
    setBusy(true);
    setError(null);
    const res = await fetch("/v1/slack/pair", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code }),
    });
    const body = await res.json().catch(() => ({}));
    if (res.ok) {
      window.location.assign(next);
      return;
    }
    setBusy(false);
    setError(typeof body.detail === "string" ? body.detail : `Linking failed (HTTP ${res.status})`);
  };

  return (
    <main className="flex min-h-full items-center justify-center bg-surface-base p-6">
      <div className="w-full max-w-md space-y-4 rounded-xl border border-surface-border bg-surface-card p-6" data-testid="link-slack-page">
        <h1 className="font-display text-lg font-semibold text-foreground">Link your Slack account</h1>
        {!code && <p className="text-sm text-neutral-600 dark:text-neutral-400">This link has no code in it. Ask the bot for a new one with its <code>pair</code> command.</p>}
        {preview && (
          <>
            <p className="text-sm text-neutral-600 dark:text-neutral-400" data-testid="link-slack-summary">
              This links the Slack user <strong className="text-foreground">{preview.slack_user_id}</strong> to the account you are signed in with.
              Their conversations with the bot become yours, and the bot runs as you from now on. Only continue if the bot sent you this link.
            </p>
            <button
              type="button"
              data-testid="link-slack-confirm"
              disabled={busy}
              onClick={() => void confirm()}
              className="rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white hover:bg-brand-hover disabled:opacity-50"
            >
              {busy ? "Linking…" : session && SESSION_ID.test(session) ? "Link and open the conversation" : "Link my account"}
            </button>
          </>
        )}
        {error && (
          <p role="alert" data-testid="link-slack-error" className="text-sm text-rose-500">
            {error}
          </p>
        )}
      </div>
    </main>
  );
}

export default function LinkSlackPage() {
  return (
    <Suspense fallback={null}>
      <LinkSlackAccount />
    </Suspense>
  );
}
