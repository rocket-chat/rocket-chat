"use client";

import React, { use, useEffect, useState } from "react";
import { RocketChatLogo } from "../../../components/RocketChatLogo";
import { MarkdownRenderer } from "../../../components/MarkdownRenderer";

interface SharedStep {
  name: string;
  kind: "edit" | "step";
  arguments: string;
}

interface SharedConversation {
  title: string;
  updated_at: number;
  expires_at: number;
  messages: { role: "user" | "assistant"; content: string; steps?: SharedStep[]; steps_cut?: number }[];
  artifacts?: { title: string; filename: string; artifact_type: string; content: string }[];
}

/** The steps behind an answer, collapsed: what the agent ran and changed (already redacted). */
function Steps({ steps, cut }: { steps: SharedStep[]; cut: number }) {
  return (
    <details className="mb-2 rounded-lg border border-surface-border text-xs" data-testid="shared-steps">
      <summary className="cursor-pointer px-3 py-1.5 text-neutral-700 dark:text-neutral-300">
        {steps.length + cut} step{steps.length + cut === 1 ? "" : "s"}
      </summary>
      <ol className="space-y-1 px-3 pb-2">
        {cut > 0 && <li className="text-neutral-600 dark:text-neutral-400">{cut} earlier steps not shown</li>}
        {steps.map((step, index) => (
          <li key={index}>
            <details>
              <summary className="cursor-pointer font-mono text-foreground">
                {step.kind === "edit" ? "✎ " : ""}
                {step.name}
              </summary>
              <pre className="mt-1 max-h-60 overflow-auto whitespace-pre-wrap rounded bg-surface-base p-2 text-neutral-700 dark:text-neutral-300">
                {step.arguments}
              </pre>
            </details>
          </li>
        ))}
      </ol>
    </details>
  );
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
            {message.role === "user" ? (
              message.content
            ) : (
              <>
                {message.steps && message.steps.length > 0 && <Steps steps={message.steps} cut={message.steps_cut ?? 0} />}
                <MarkdownRenderer content={message.content} />
              </>
            )}
          </li>
        ))}
      </ol>

      {conversation?.artifacts && conversation.artifacts.length > 0 && (
        <section className="mt-8 space-y-3" aria-label="Artifacts" data-testid="shared-artifacts">
          <h2 className="text-sm font-semibold text-foreground">Artifacts</h2>
          {conversation.artifacts.map((artifact) => (
            <details key={artifact.filename} className="rounded-lg border border-surface-border text-sm">
              <summary className="cursor-pointer px-3 py-2 text-foreground">
                {artifact.title} <span className="font-mono text-xs text-neutral-600 dark:text-neutral-400">{artifact.filename}</span>
              </summary>
              <div className="px-3 pb-3">
                {artifact.artifact_type === "markdown" ? (
                  <MarkdownRenderer content={artifact.content} />
                ) : (
                  <pre className="max-h-96 overflow-auto whitespace-pre-wrap text-xs text-foreground">{artifact.content}</pre>
                )}
              </div>
            </details>
          ))}
        </section>
      )}
    </main>
  );
}
