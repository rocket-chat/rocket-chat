"use client";

import React, { useState } from "react";
import { useRouter } from "next/navigation";
import { Play } from "lucide-react";
import { Field, Textarea } from "../ui/Field";
import { Modal } from "../ui/Modal";
import type { AgentDraft } from "./draft";

const DEFAULT_PROMPT = "Introduce yourself in two sentences, then list the tools you can use.";

interface TestRunButtonProps {
  draft: AgentDraft;
  /** The agent exists on the server as shown: a test run uses the saved agent, never the draft. */
  ready: boolean;
}

function blockedReason(draft: AgentDraft, ready: boolean): string | null {
  if (draft.kind !== "agent") return "Subagent roles run inside a session; test the agent that delegates to them.";
  if (!draft.enabled) return "Enable the agent to test it.";
  if (!ready) return "Save the agent before a test run.";
  return null;
}

/** Starts a session with the saved agent and a short prompt, then opens it. */
export const TestRunButton: React.FC<TestRunButtonProps> = ({ draft, ready }) => {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [prompt, setPrompt] = useState(DEFAULT_PROMPT);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const blocked = blockedReason(draft, ready);

  const start = async () => {
    setBusy(true);
    setError(null);
    const res = await fetch("/v1/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: `Test run: ${draft.name}`, agent_id: draft.id, initial_prompt: prompt.trim() }),
    }).catch(() => null);
    setBusy(false);
    if (!res?.ok) {
      const body: { detail?: unknown } = res ? await res.json().catch(() => ({})) : {};
      setError(typeof body.detail === "string" ? body.detail : "The test run did not start.");
      return;
    }
    const session: { session_id: string } = await res.json();
    setOpen(false);
    router.push(`/chat/${session.session_id}`);
  };

  return (
    <>
      <button
        type="button"
        data-testid="agent-test-run"
        disabled={blocked !== null}
        title={blocked ?? "Start a session with this agent"}
        onClick={() => setOpen(true)}
        className="inline-flex items-center gap-1 rounded-lg border border-border px-2 py-1.5 text-xs text-foreground hover:bg-surface-elevated disabled:opacity-50"
      >
        <Play className="h-3.5 w-3.5" aria-hidden="true" /> Test run
      </button>
      {open && (
        <Modal title={`Test run: ${draft.name}`} onClose={() => setOpen(false)} className="sm:max-w-lg" testId="agent-test-run-dialog">
          <p className="text-xs text-neutral-600 dark:text-neutral-400">
            Starts a new session with the saved agent; it is listed with your sessions and costs like any other.
          </p>
          <Field label="Prompt">
            {(control) => (
              <Textarea
                {...control}
                data-testid="agent-test-run-prompt"
                rows={4}
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                className="text-xs"
              />
            )}
          </Field>
          {error && (
            <p role="alert" className="text-xs text-rose-500">
              {error}
            </p>
          )}
          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={() => setOpen(false)}
              className="rounded-lg border border-border px-3 py-1.5 text-xs text-foreground hover:bg-surface-elevated"
            >
              Cancel
            </button>
            <button
              type="button"
              data-testid="agent-test-run-start"
              disabled={busy || !prompt.trim()}
              onClick={() => void start()}
              className="inline-flex items-center gap-1 rounded-lg bg-brand px-3 py-1.5 text-xs font-semibold text-white hover:bg-brand-hover disabled:opacity-50"
            >
              <Play className="h-3.5 w-3.5" aria-hidden="true" /> {busy ? "Starting…" : "Start"}
            </button>
          </div>
        </Modal>
      )}
    </>
  );
};
