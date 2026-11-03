"use client";

import React, { useEffect, useId, useRef, useState } from "react";
import { AlertTriangle, Info } from "lucide-react";
import { Button } from "../ui/Button";
import { Field, Select } from "../ui/Field";
import { useTeams } from "../../lib/teams";
import { audienceOf, ownerFields, ownerLabel, type OwnerKey } from "../../lib/ownership";

/** What the API can move (`POST /v1/ownership/preview`). */
export type MovableKind = "agent" | "skill" | "mcp_server" | "knowledge_base" | "credential" | "sandbox" | "mission" | "template";

export interface MoveTarget {
  kind: MovableKind;
  id: string;
}

interface Problem {
  kind: string;
  id: string;
  name: string;
  detail: string;
  /** The move would break it (the move is refused); otherwise a note. */
  breaks: boolean;
}

interface Preview {
  audience: string;
  allowed: boolean;
  reason: string;
  problems: Problem[];
}

/** What the API says a move would do: who uses the object after it, and what it would break. */
function useMovePreview(target: MoveTarget | undefined, to: OwnerKey, from: OwnerKey): Preview | null | "loading" {
  const [preview, setPreview] = useState<Preview | null | "loading">(target ? "loading" : null);
  useEffect(() => {
    if (!target || to === from) {
      setPreview(null);
      return;
    }
    let cancelled = false;
    setPreview("loading");
    fetch("/v1/ownership/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind: target.kind, id: target.id, ...ownerFields(to) }),
    })
      .then(async (res) => {
        const body = (await res.json().catch(() => ({}))) as Partial<Preview> & { detail?: unknown };
        if (cancelled) return;
        if (!res.ok) {
          const reason = typeof body.detail === "string" ? body.detail : "This move cannot be checked.";
          setPreview({ audience: "", allowed: false, reason, problems: [] });
          return;
        }
        setPreview({ audience: body.audience ?? "", allowed: body.allowed !== false, reason: body.reason ?? "", problems: body.problems ?? [] });
      })
      .catch(() => {
        if (!cancelled) setPreview(null);
      });
    return () => {
      cancelled = true;
    };
  }, [target, to, from]);
  return preview;
}

interface MoveDialogProps {
  name: string;
  from: OwnerKey;
  /** The owner proposed; with `options`, the person chooses it in the dialog. */
  to: OwnerKey;
  options?: OwnerKey[];
  target?: MoveTarget;
  onDone: (to: OwnerKey | null) => void;
}

/**
 * Moving an object to another owner: who uses it today and after, and, from the API, what the
 * move would break (an agent's key its new owner cannot use, a sandbox type an agent still
 * needs...). A move that breaks something cannot be confirmed.
 */
export const MoveDialog: React.FC<MoveDialogProps> = ({ name, from, to: proposed, options, target, onDone }) => {
  const { teams } = useTeams();
  const [to, setTo] = useState<OwnerKey>(proposed);
  const preview = useMovePreview(target, to, from);
  const titleId = useId();
  const cancelRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const previouslyFocused = document.activeElement as HTMLElement | null;
    cancelRef.current?.focus();
    return () => previouslyFocused?.focus?.();
  }, []);

  const blocked = preview === "loading" || (!!preview && !preview.allowed) || to === from;
  const onKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      onDone(null);
      return;
    }
    if (event.key !== "Tab") return;
    // Keep Tab inside the dialog.
    const focusable = Array.from(dialogRef.current?.querySelectorAll<HTMLElement>("button:not([disabled]), select") ?? []);
    if (focusable.length === 0) return;
    const index = focusable.indexOf(document.activeElement as HTMLElement);
    const next = event.shiftKey ? (index <= 0 ? focusable.length - 1 : index - 1) : (index + 1) % focusable.length;
    event.preventDefault();
    focusable[next].focus();
  };

  const label = (key: OwnerKey) => (key === "me" ? "you" : ownerLabel(key, teams));
  return (
    <div
      className="fixed inset-0 z-[80] flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm"
      onMouseDown={(event) => event.target === event.currentTarget && onDone(null)}
    >
      <div
        ref={dialogRef}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby={titleId}
        data-testid="confirm-dialog"
        onKeyDown={onKeyDown}
        className="w-full max-w-md space-y-4 rounded-2xl border border-surface-border bg-surface-base p-5 shadow-2xl"
      >
        <h2 id={titleId} className="text-sm font-semibold text-foreground">
          Move &quot;{name}&quot;{options ? "" : ` to ${label(to)}`}?
        </h2>
        {options && (
          <Field label="New owner">
            {(control) => (
              <Select {...control} data-testid="move-owner" value={to} onChange={(e) => setTo(e.target.value as OwnerKey)}>
                {[from, ...options.filter((o) => o !== from)].map((key) => (
                  <option key={key} value={key}>
                    {key === from ? `${ownerLabel(key, teams)} (now)` : key === "me" ? "Me (personal)" : key === "org" ? "Organization" : `Team · ${ownerLabel(key, teams)}`}
                  </option>
                ))}
              </Select>
            )}
          </Field>
        )}
        {to !== from && (
          <p data-testid="move-audience" className="text-xs leading-relaxed text-neutral-700 dark:text-neutral-300">
            It is used today by {audienceOf(from, teams)}. After the move it will be used by {audienceOf(to, teams)}, and changed by those who manage that owner.
            {preview && preview !== "loading" && preview.audience ? ` ${preview.audience}` : ""}
          </p>
        )}
        {preview === "loading" && <p className="text-xs text-neutral-600 dark:text-neutral-400">Checking what the move changes…</p>}
        {preview && preview !== "loading" && !preview.allowed && preview.reason && (
          <p role="alert" data-testid="move-refused" className="flex items-start gap-1.5 text-xs text-rose-700 dark:text-rose-300">
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" /> {preview.reason}
          </p>
        )}
        {preview && preview !== "loading" && preview.problems.length > 0 && (
          <ul data-testid="move-problems" className="space-y-1.5 rounded-lg border border-surface-border p-2.5 text-xs">
            {preview.problems.map((problem) => (
              <li key={`${problem.kind}-${problem.id}`} data-testid={problem.breaks ? "move-problem-breaks" : "move-problem-note"} className="flex items-start gap-1.5">
                {problem.breaks ? (
                  <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-rose-600 dark:text-rose-400" aria-label="Breaks" />
                ) : (
                  <Info className="mt-0.5 h-3.5 w-3.5 shrink-0 text-neutral-500" aria-label="Note" />
                )}
                <span className="text-neutral-700 dark:text-neutral-300">
                  <span className="font-medium text-foreground">{problem.name || problem.id}</span> ({problem.kind.replace("_", " ")}): {problem.detail}
                </span>
              </li>
            ))}
          </ul>
        )}
        <div className="flex justify-end gap-2">
          <Button ref={cancelRef} size="sm" data-testid="confirm-cancel" onClick={() => onDone(null)}>
            Cancel
          </Button>
          <Button size="sm" variant="primary" data-testid="confirm-accept" disabled={blocked} onClick={() => onDone(to)}>
            Move
          </Button>
        </div>
      </div>
    </div>
  );
};
