"use client";

import React, { useState } from "react";
import { ClipboardCheck, Plus, Trash2 } from "lucide-react";
import { useMissionStore } from "../lib/store";
import type { ApprovalRequestInfo } from "../types/mission";
import { MarkdownRenderer } from "./MarkdownRenderer";
import { Field, Select, Textarea } from "./ui/Field";

/** The gate ``exit_plan_mode`` raises (``PLAN_APPROVAL_ACTION`` on the server). */
export const PLAN_APPROVAL_ACTION = "execute_plan";

const CONTINUE_MODES: { value: string; label: string }[] = [
  { value: "ask", label: "Ask before every change" },
  { value: "auto_edit", label: "Edit files freely, ask before commands" },
  { value: "auto", label: "Work on its own (irreversible actions still ask)" },
  { value: "bypass", label: "Bypass (nothing waits)" },
];

interface EditableTask {
  id: string;
  title: string;
}

/** A proposed plan: approve it in a chosen mode (edited or not), or send it back with notes. */
export const PlanCard: React.FC<{ approval: ApprovalRequestInfo }> = ({ approval }) => {
  const sessionId = useMissionStore((s) => s.session?.session_id);
  const checklist = useMissionStore((s) => s.checklist);
  const [tasks, setTasks] = useState<EditableTask[]>(() => checklist.map((t) => ({ id: t.id, title: t.title })));
  const [editing, setEditing] = useState(false);
  const [mode, setMode] = useState("auto_edit");
  const [feedback, setFeedback] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const decide = async (approved: boolean) => {
    if (!sessionId) return;
    setBusy(true);
    setError(null);
    const res = await fetch(`/v1/sessions/${sessionId}/plan/decision`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        action_id: approval.action_id,
        approved,
        ...(approved ? { permission_mode: mode } : {}),
        ...(editing ? { checklist: tasks.filter((t) => t.title.trim()).map((t) => ({ id: t.id, title: t.title.trim() })) } : {}),
        feedback: feedback.trim(),
      }),
    }).catch(() => null);
    setBusy(false);
    if (!res || !res.ok) {
      const body = res ? await res.json().catch(() => ({})) : {};
      setError(typeof body.detail === "string" ? body.detail : "The decision was not saved.");
      return;
    }
    useMissionStore.getState().setActiveApproval(null);
  };

  return (
    <section
      data-testid="plan-card"
      aria-label="Proposed plan"
      className="rounded-xl border border-indigo-500/50 bg-indigo-500/5 px-4 py-3 shadow-sm"
    >
      <div className="flex items-center gap-2">
        <ClipboardCheck className="h-4 w-4 text-indigo-700 dark:text-indigo-300" aria-hidden="true" />
        <h3 className="text-sm font-semibold text-foreground">The agent proposes a plan</h3>
      </div>
      {approval.description && (
        <div className="mt-2 max-h-56 overflow-auto rounded-lg bg-surface-base/70 p-2 text-sm text-foreground">
          <MarkdownRenderer content={approval.description} />
        </div>
      )}

      {tasks.length > 0 && (
        <div className="mt-3 space-y-1">
          <div className="flex items-center justify-between">
            <h4 className="text-xs font-medium text-neutral-700 dark:text-neutral-300">Checklist</h4>
            <button
              type="button"
              data-testid="plan-edit"
              onClick={() => setEditing((v) => !v)}
              className="text-xs text-brand hover:underline"
            >
              {editing ? "Done editing" : "Edit"}
            </button>
          </div>
          <ol className="list-decimal space-y-1 pl-5 text-sm text-foreground">
            {tasks.map((task, index) => (
              <li key={task.id || index}>
                {editing ? (
                  <span className="flex items-center gap-1">
                    <input
                      aria-label={`Task ${index + 1}`}
                      value={task.title}
                      onChange={(e) =>
                        setTasks((list) => list.map((t, i) => (i === index ? { ...t, title: e.target.value } : t)))
                      }
                      className="w-full rounded border border-border bg-surface-base px-2 py-1 text-sm"
                    />
                    <button
                      type="button"
                      aria-label={`Remove task ${index + 1}`}
                      onClick={() => setTasks((list) => list.filter((_, i) => i !== index))}
                      className="rounded p-1 text-neutral-600 hover:bg-surface-elevated dark:text-neutral-400"
                    >
                      <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                    </button>
                  </span>
                ) : (
                  task.title
                )}
              </li>
            ))}
          </ol>
          {editing && (
            <button
              type="button"
              onClick={() => setTasks((list) => [...list, { id: `edit-${list.length + 1}`, title: "" }])}
              className="flex items-center gap-1 text-xs text-brand hover:underline"
            >
              <Plus className="h-3 w-3" aria-hidden="true" /> Add a task
            </button>
          )}
        </div>
      )}

      <div className="mt-3 flex flex-wrap items-end gap-3">
        <Field label="Then work in">
          {(control) => (
            <Select {...control} data-testid="plan-mode" value={mode} onChange={(e) => setMode(e.target.value)} className="max-w-xs">
              {CONTINUE_MODES.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </Select>
          )}
        </Field>
      </div>
      <Field label="Notes for the agent (optional)" className="mt-2">
        {(control) => (
          <Textarea {...control} data-testid="plan-feedback" rows={2} value={feedback} onChange={(e) => setFeedback(e.target.value)} />
        )}
      </Field>
      {error && (
        <p role="alert" className="mt-2 text-xs text-red-700 dark:text-red-300">
          {error}
        </p>
      )}
      <div className="mt-3 flex flex-wrap gap-2">
        <button
          type="button"
          data-testid="plan-approve"
          disabled={busy}
          onClick={() => void decide(true)}
          className="rounded-lg bg-emerald-700 px-3.5 py-1.5 text-xs font-semibold text-white hover:bg-emerald-600 disabled:opacity-50"
        >
          Approve plan
        </button>
        <button
          type="button"
          data-testid="plan-reject"
          disabled={busy}
          onClick={() => void decide(false)}
          className="rounded-lg border border-border bg-surface-elevated px-3.5 py-1.5 text-xs font-semibold text-foreground hover:bg-surface-border disabled:opacity-50"
        >
          Send back
        </button>
      </div>
    </section>
  );
};
