"use client";

import React, { useEffect, useState } from "react";

interface PairingContext {
  pairing_required: boolean;
  slack_user_id?: string | null;
}

type Sending = "idle" | "sending" | "sent";

/**
 * Shown in place of a session the person may not open. When it is a Slack direct message of
 * someone who never linked an account, the person can ask the bot to send that Slack user a
 * pairing link: the conversation is theirs to claim, not public.
 */
export function SessionAccessPrompt({ sessionId }: { sessionId: string }) {
  const [context, setContext] = useState<PairingContext | null>(null);
  const [sending, setSending] = useState<Sending>("idle");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void fetch(`/v1/slack/pairing-context?session_id=${encodeURIComponent(sessionId)}`)
      .then((res) => (res.ok ? res.json() : { pairing_required: false }))
      .then((body: PairingContext) => setContext(body))
      .catch(() => setContext({ pairing_required: false }));
  }, [sessionId]);

  const send = async () => {
    setSending("sending");
    setError(null);
    const res = await fetch("/v1/slack/pairing-requests", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId }),
    });
    if (res.ok) {
      setSending("sent");
      return;
    }
    const body = await res.json().catch(() => ({}));
    setSending("idle");
    setError(typeof body.detail === "string" ? body.detail : `Could not send the link (HTTP ${res.status})`);
  };

  return (
    <main className="flex h-full w-full items-center justify-center bg-surface-base p-6" data-testid="session-access-prompt">
      <div className="w-full max-w-md space-y-4 rounded-xl border border-surface-border bg-surface-card p-6">
        <h1 className="font-display text-lg font-semibold text-foreground">You cannot open this conversation</h1>
        {context === null && <p className="text-sm text-neutral-600 dark:text-neutral-400">Checking…</p>}
        {context && !context.pairing_required && (
          <p className="text-sm text-neutral-600 dark:text-neutral-400">
            It belongs to someone else and has not been shared with you. Ask its owner to share it.
          </p>
        )}
        {context?.pairing_required && (
          <>
            <p className="text-sm text-neutral-600 dark:text-neutral-400">
              It was started in Slack by user <strong className="text-foreground">{context.slack_user_id}</strong>, whose Slack
              account is not linked to Rocket Chat. If that is you, we can send you a link in Slack to link it and open this conversation.
            </p>
            {sending !== "sent" && (
              <button
                type="button"
                data-testid="send-pairing-link"
                disabled={sending === "sending"}
                onClick={() => void send()}
                className="rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white hover:bg-brand-hover disabled:opacity-50"
              >
                {sending === "sending" ? "Sending…" : "Send me a link in Slack"}
              </button>
            )}
            {sending === "sent" && (
              <p role="status" data-testid="pairing-link-sent" className="text-sm text-foreground">
                Link sent. Open your Slack messages with the bot and follow it; this conversation opens once your account is linked.
              </p>
            )}
          </>
        )}
        {error && (
          <p role="alert" data-testid="pairing-link-error" className="text-sm text-rose-500">
            {error}
          </p>
        )}
      </div>
    </main>
  );
}
