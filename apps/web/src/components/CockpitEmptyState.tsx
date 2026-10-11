"use client";

import React, { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { BookMarked, Bug, FileSearch, FlaskConical, GitPullRequest } from "lucide-react";
import { useMissionStore } from "../lib/store";
import { RocketChatIcon } from "./RocketChatLogo";

interface TemplateSummary {
  id: string;
  name: string;
}

const STARTERS: { icon: React.ReactNode; title: string; prompt: string }[] = [
  {
    icon: <Bug className="h-4 w-4" aria-hidden="true" />,
    title: "Fix a failing test",
    prompt: "Run the test suite, find the failing test and fix its root cause.",
  },
  {
    icon: <GitPullRequest className="h-4 w-4" aria-hidden="true" />,
    title: "Review recent changes",
    prompt: "Review my latest changes for bugs, risky edits and missing tests.",
  },
  {
    icon: <FileSearch className="h-4 w-4" aria-hidden="true" />,
    title: "Explain this codebase",
    prompt: "Give me a tour of this codebase: structure, entry points and how to run it.",
  },
  {
    icon: <FlaskConical className="h-4 w-4" aria-hidden="true" />,
    title: "Write tests",
    prompt: "Find the least-tested module and add focused unit tests for it.",
  },
];

/** What a new conversation shows: a prompt to start from, not an empty page. */
export const CockpitEmptyState: React.FC = () => {
  const router = useRouter();
  const seedComposer = useMissionStore((state) => state.seedComposer);
  const [templates, setTemplates] = useState<TemplateSummary[]>([]);

  useEffect(() => {
    let cancelled = false;
    fetch("/v1/templates")
      .then((res) => (res.ok ? res.json() : []))
      .then((data: TemplateSummary[]) => {
        if (!cancelled && Array.isArray(data)) setTemplates(data.slice(0, 4));
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  const startTemplate = async (id: string) => {
    const res = await fetch(`/v1/templates/${id}/start`, { method: "POST" });
    if (!res.ok) return;
    const session = (await res.json()) as { session_id: string };
    router.push(`/chat/${session.session_id}`);
  };

  return (
    <div data-testid="cockpit-empty-state" className="mx-auto my-auto flex w-full max-w-2xl flex-col gap-8 py-10">
      <div className="space-y-3 text-center">
        <div className="mx-auto flex h-11 w-11 items-center justify-center rounded-xl border border-brand/30 bg-brand/10 text-brand">
          <RocketChatIcon className="h-6 w-6" />
        </div>
        <h2 className="font-display text-2xl font-semibold tracking-tight text-foreground">What should we work on?</h2>
        <p className="text-sm text-neutral-600 dark:text-neutral-400">
          Describe a task below, or start from one of these. The agent works in an isolated sandbox and asks before anything risky.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
        {STARTERS.map((starter) => (
          <button
            key={starter.title}
            type="button"
            data-testid="starter-prompt"
            onClick={() => seedComposer(starter.prompt)}
            className="group flex cursor-pointer items-start gap-3 rounded-xl border border-surface-border bg-surface-card p-3.5 text-left transition-colors hover:border-brand/50 hover:bg-surface-elevated focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/60"
          >
            <span className="mt-0.5 text-brand">{starter.icon}</span>
            <span className="min-w-0">
              <span className="block text-sm font-medium text-foreground">{starter.title}</span>
              <span className="mt-0.5 block text-xs leading-relaxed text-neutral-600 dark:text-neutral-400">{starter.prompt}</span>
            </span>
          </button>
        ))}
      </div>

      {templates.length > 0 && (
        <div className="space-y-2">
          <h3 className="flex items-center gap-1.5 text-xs font-medium text-neutral-600 dark:text-neutral-400">
            <BookMarked className="h-3.5 w-3.5" aria-hidden="true" />
            Your templates
          </h3>
          <div className="flex flex-wrap gap-2">
            {templates.map((template) => (
              <button
                key={template.id}
                type="button"
                onClick={() => void startTemplate(template.id)}
                className="cursor-pointer rounded-full border border-surface-border bg-surface-card px-3 py-1 text-xs text-foreground transition-colors hover:border-brand/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/60"
              >
                {template.name}
              </button>
            ))}
          </div>
        </div>
      )}

      <p className="text-center text-xs text-neutral-600 dark:text-neutral-400">
        <kbd className="rounded border border-surface-border bg-surface-card px-1.5 py-0.5 font-mono text-[10px] text-neutral-600 dark:text-neutral-400">/</kbd> for commands ·{" "}
        <kbd className="rounded border border-surface-border bg-surface-card px-1.5 py-0.5 font-mono text-[10px] text-neutral-600 dark:text-neutral-400">⌘K</kbd> new conversation
      </p>
    </div>
  );
};
