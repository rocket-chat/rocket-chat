"use client";

import React, { useCallback, useEffect, useState } from "react";
import { CheckCircle2, CircleDashed, ExternalLink, GitBranch, GitCommit, MessageSquare, RefreshCw, XCircle } from "lucide-react";
import { useMissionStore } from "../lib/store";
import { Field, Input, Textarea } from "./ui/Field";

interface Check {
  name: string;
  status: string;
  conclusion: string | null;
  url: string;
}

interface Thread {
  id: string;
  resolved: boolean;
  path: string;
  line: number | null;
  comments: [string, string][];
}

interface PullRequest {
  number: number;
  url: string;
  title: string;
  state: string;
  draft: boolean;
  head_sha: string;
  checks: Check[];
  threads: Thread[];
}

interface GitState {
  repository: string;
  folder: string;
  branch: string;
  base: string;
  ahead: number;
  behind: number;
  uncommitted: number;
  commits: { sha: string; author: string; timestamp: number; subject: string }[];
  pull_request: PullRequest | null;
  pull_request_error: string;
  publish_as: string;
  signing: string;
}

type Action = "commit" | "push" | "pull-request";

const CheckIcon: React.FC<{ check: Check }> = ({ check }) => {
  if (check.conclusion === "success") return <CheckCircle2 className="h-3.5 w-3.5 text-emerald-700 dark:text-emerald-400" aria-label="passed" />;
  if (check.conclusion && check.conclusion !== "neutral" && check.conclusion !== "skipped")
    return <XCircle className="h-3.5 w-3.5 text-red-700 dark:text-red-400" aria-label={check.conclusion} />;
  return <CircleDashed className="h-3.5 w-3.5 text-amber-700 dark:text-amber-400" aria-label={check.status} />;
};

/** The active repository's branch, its pull request (checks, review threads) and the buttons
 * that publish it the way the agent would for you. */
export const GitPanel: React.FC = () => {
  const session = useMissionStore((s) => s.session);
  const busyTurn = useMissionStore((s) => s.isExecuting);
  const sessionId = session && !session.is_draft ? session.session_id : null;
  const [state, setState] = useState<GitState | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("");
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [result, setResult] = useState("");
  const [running, setRunning] = useState<Action | null>(null);

  const load = useCallback(async () => {
    if (!sessionId) return;
    setLoading(true);
    const res = await fetch(`/v1/sessions/${sessionId}/git`).catch(() => null);
    setLoading(false);
    if (!res || !res.ok) {
      const detail = res ? ((await res.json().catch(() => ({}))) as { detail?: string }).detail : undefined;
      setError(typeof detail === "string" ? detail : "Could not read the repository.");
      setState(null);
      return;
    }
    setError("");
    setState(await res.json());
  }, [sessionId]);

  useEffect(() => {
    void load();
  }, [load]);

  const act = async (action: Action, payload: Record<string, unknown> = {}) => {
    if (!sessionId) return;
    setRunning(action);
    setResult("");
    const url = {
      commit: `/v1/sessions/${sessionId}/git/commit`,
      push: `/v1/sessions/${sessionId}/git/push`,
      "pull-request": `/v1/sessions/${sessionId}/git/pull-request`,
    }[action];
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }).catch(() => null);
    setRunning(null);
    const answer = res ? ((await res.json().catch(() => ({}))) as { summary?: string; detail?: string }) : {};
    setResult(answer.summary ?? answer.detail ?? "The request failed.");
    if (res?.ok && action === "commit") setMessage("");
    await load();
  };

  if (!sessionId) return <p className="p-4 text-xs text-neutral-600 dark:text-neutral-400">Start a conversation to see its repository.</p>;
  if (error) {
    return (
      <div className="space-y-2 p-4 text-xs">
        <p role="alert" className="text-neutral-700 dark:text-neutral-300">{error}</p>
        <button type="button" onClick={() => void load()} className="text-brand hover:underline">Try again</button>
      </div>
    );
  }
  if (!state) return <p className="p-4 text-xs text-neutral-600 dark:text-neutral-400">{loading ? "Reading the repository…" : ""}</p>;
  const pull = state.pull_request;
  const disabled = busyTurn || running !== null;

  return (
    <div data-testid="git-panel" className="h-full space-y-4 overflow-y-auto p-4 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <GitBranch className="h-4 w-4 text-brand" aria-hidden="true" />
        <span className="font-mono text-foreground" data-testid="git-branch">{state.branch}</span>
        <span className="text-xs text-neutral-600 dark:text-neutral-400">
          into {state.base} · {state.ahead} ahead, {state.behind} behind · {state.uncommitted} uncommitted
        </span>
        <button type="button" aria-label="Refresh" onClick={() => void load()} className="ml-auto rounded p-1 hover:bg-surface-elevated">
          <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} aria-hidden="true" />
        </button>
      </div>
      <p className="text-xs text-neutral-600 dark:text-neutral-400">
        Publishes as {state.publish_as === "bot" ? "the GitHub App" : "you"}; signing: {state.signing}. {state.repository}
      </p>

      {busyTurn && <p className="text-xs text-amber-800 dark:text-amber-300">The agent is working; publishing waits for the turn to finish.</p>}

      <section aria-label="Commit" className="space-y-2">
        <Field label="Commit message">
          {(control) => <Input {...control} data-testid="git-commit-message" value={message} onChange={(e) => setMessage(e.target.value)} />}
        </Field>
        <div className="flex flex-wrap gap-2">
          <button type="button" data-testid="git-commit" disabled={disabled || !message.trim() || state.uncommitted === 0} onClick={() => void act("commit", { message })} className="rounded-lg bg-brand px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-50">
            {running === "commit" ? "Committing…" : "Commit"}
          </button>
          <button type="button" data-testid="git-push" disabled={disabled || state.ahead === 0} onClick={() => void act("push")} className="rounded-lg border border-border px-3 py-1.5 text-xs font-semibold text-foreground disabled:opacity-50">
            {running === "push" ? "Pushing…" : "Push"}
          </button>
        </div>
      </section>

      {state.commits.length > 0 && (
        <section aria-label="Commits" className="space-y-1">
          <h4 className="text-xs font-semibold text-foreground">Commits on this branch</h4>
          <ul className="space-y-0.5 font-mono text-xs">
            {state.commits.map((c) => (
              <li key={c.sha} className="flex items-center gap-2 text-neutral-700 dark:text-neutral-300">
                <GitCommit className="h-3 w-3 shrink-0" aria-hidden="true" />
                <span>{c.sha.slice(0, 8)}</span>
                <span className="truncate font-sans">{c.subject}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {pull ? (
        <section aria-label="Pull request" className="space-y-2" data-testid="git-pull-request">
          <a href={pull.url} target="_blank" rel="noreferrer" className="flex items-center gap-1.5 font-medium text-brand hover:underline">
            #{pull.number} {pull.title} <ExternalLink className="h-3 w-3" aria-hidden="true" />
          </a>
          <p className="text-xs text-neutral-600 dark:text-neutral-400">
            {pull.draft ? "Draft · " : ""}{pull.state} · head {pull.head_sha.slice(0, 8)}
          </p>
          {pull.checks.length > 0 && (
            <ul className="space-y-0.5 text-xs" aria-label="Checks">
              {pull.checks.map((check) => (
                <li key={check.name} className="flex items-center gap-1.5">
                  <CheckIcon check={check} />
                  {check.url ? <a href={check.url} target="_blank" rel="noreferrer" className="hover:underline">{check.name}</a> : check.name}
                </li>
              ))}
            </ul>
          )}
          {pull.threads.length > 0 && (
            <ul className="space-y-2 text-xs" aria-label="Review threads">
              {pull.threads.map((thread) => (
                <li key={thread.id} className={`rounded border border-border p-2 ${thread.resolved ? "opacity-60" : ""}`}>
                  <p className="flex items-center gap-1 font-mono text-neutral-700 dark:text-neutral-300">
                    <MessageSquare className="h-3 w-3" aria-hidden="true" />
                    {thread.path}{thread.line ? `:${thread.line}` : ""} {thread.resolved ? "(resolved)" : ""}
                  </p>
                  {thread.comments.map(([author, text], i) => (
                    <p key={i} className="mt-1 text-foreground"><strong>{author}</strong>: {text}</p>
                  ))}
                </li>
              ))}
            </ul>
          )}
        </section>
      ) : (
        <section aria-label="Open a pull request" className="space-y-2">
          {state.pull_request_error && <p className="text-xs text-neutral-600 dark:text-neutral-400">{state.pull_request_error}</p>}
          <Field label="Pull request title">
            {(control) => <Input {...control} data-testid="git-pr-title" value={title} onChange={(e) => setTitle(e.target.value)} />}
          </Field>
          <Field label="Description">
            {(control) => <Textarea {...control} rows={3} value={body} onChange={(e) => setBody(e.target.value)} />}
          </Field>
          <button type="button" data-testid="git-open-pr" disabled={disabled || !title.trim()} onClick={() => void act("pull-request", { title, body })} className="rounded-lg bg-brand px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-50">
            {running === "pull-request" ? "Opening…" : "Open pull request"}
          </button>
        </section>
      )}

      {result && (
        <pre data-testid="git-result" role="status" className="whitespace-pre-wrap rounded border border-border bg-surface-base p-2 text-xs text-foreground">
          {result}
        </pre>
      )}
    </div>
  );
};
