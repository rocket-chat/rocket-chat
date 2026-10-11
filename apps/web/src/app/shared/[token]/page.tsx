"use client";

import React, { use, useEffect, useState } from "react";
import { RocketChatLogo } from "../../../components/RocketChatLogo";
import { MarkdownRenderer } from "../../../components/MarkdownRenderer";

interface SharedConversation {
  title: string;
  updated_at: number;
  expires_at: number;
  messages: { role: "user" | "assistant"; content: string }[];
}

/** A read-only view of a conversation, opened from a public link without signing in. */
export default function SharedConversationPage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = use(params);
  const [conversation, setConversation] = useState<SharedConversation | null>(null);
  const [unavailable, setUnavailable] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetch(`/v1/shared/${encodeURIComponent(token)}`, { credentials: "omit", cache: "no-store" })
      .then(async (res) => {
        if (cancelled) return;
        if (!res.ok) return setUnavailable(true);
        setConversation(await res.json());
      })
      .catch(() => !cancelled && setUnavailable(true));
    return () => {
      cancelled = true;
    };
  }, [token]);

  return (
    <main className="mx-auto min-h-dvh w-full max-w-3xl px-4 py-6 sm:px-6">
      <header className="mb-6 flex items-center gap-3 border-b border-surface-border pb-4">
        <RocketChatLogo className="h-6 w-6" />
        <div className="min-w-0">
          <h1 data-testid="shared-title" className="truncate text-base font-semibold text-foreground">
            {conversation?.title ?? "Shared conversation"}
          </h1>
          <p className="text-xs text-neutral-500">Read-only view shared with a link</p>
        </div>
      </header>

      {unavailable && (
        <p data-testid="shared-unavailable" className="rounded-lg border border-surface-border bg-surface-card p-4 text-sm text-neutral-600 dark:text-neutral-300">
          This link is invalid, has expired or was revoked.
        </p>
      )}

      <ol className="space-y-4">
        {conversation?.messages.map((message, index) => (
          <li
            key={index}
            data-testid={`shared-${message.role}`}
            className={
              message.role === "user"
                ? "ml-auto max-w-[85%] rounded-2xl bg-surface-elevated px-4 py-2.5 text-sm text-foreground"
                : "text-sm text-foreground"
            }
          >
            {message.role === "user" ? message.content : <MarkdownRenderer content={message.content} />}
          </li>
        ))}
      </ol>
    </main>
  );
}
