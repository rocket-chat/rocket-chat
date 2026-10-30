"use client";

import React, { useCallback, useEffect, useId, useRef, useState } from "react";
import { Button } from "./Button";

export interface ConfirmOptions {
  title: string;
  message?: string;
  /** The label of the button that goes ahead ("Delete", "Remove"). */
  confirmLabel?: string;
  /** "danger" for what cannot be undone. */
  tone?: "danger" | "default";
}

interface Pending extends ConfirmOptions {
  resolve: (confirmed: boolean) => void;
}

/**
 * An accessible stand-in for `window.confirm`: an alert dialog that takes focus on its safe
 * button, keeps Tab inside, closes on Escape (as a "no") and hands focus back afterwards.
 */
const ConfirmDialog: React.FC<{ pending: Pending; onDone: (confirmed: boolean) => void }> = ({ pending, onDone }) => {
  const titleId = useId();
  const messageId = useId();
  const cancelRef = useRef<HTMLButtonElement>(null);
  const confirmRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const previouslyFocused = document.activeElement as HTMLElement | null;
    cancelRef.current?.focus();
    return () => previouslyFocused?.focus?.();
  }, []);

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      onDone(false);
    } else if (event.key === "Tab") {
      event.preventDefault();
      const next = document.activeElement === cancelRef.current ? confirmRef.current : cancelRef.current;
      next?.focus();
    }
  };

  return (
    <div
      className="fixed inset-0 z-[80] flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm"
      onMouseDown={(event) => event.target === event.currentTarget && onDone(false)}
    >
      <div
        role="alertdialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={pending.message ? messageId : undefined}
        data-testid="confirm-dialog"
        onKeyDown={onKeyDown}
        className="w-full max-w-md space-y-4 rounded-2xl border border-surface-border bg-surface-base p-5 shadow-2xl"
      >
        <h2 id={titleId} className="text-sm font-semibold text-foreground">
          {pending.title}
        </h2>
        {pending.message && (
          <p id={messageId} className="text-xs leading-relaxed text-neutral-700 dark:text-neutral-300">
            {pending.message}
          </p>
        )}
        <div className="flex justify-end gap-2">
          <Button ref={cancelRef} size="sm" data-testid="confirm-cancel" onClick={() => onDone(false)}>
            Cancel
          </Button>
          <Button
            ref={confirmRef}
            size="sm"
            variant={pending.tone === "danger" ? "danger" : "primary"}
            data-testid="confirm-accept"
            onClick={() => onDone(true)}
          >
            {pending.confirmLabel ?? "Confirm"}
          </Button>
        </div>
      </div>
    </div>
  );
};

/**
 * `const [confirm, dialog] = useConfirm()`: `await confirm({...})` resolves true or false once the
 * person answers; render `dialog` once in the component.
 */
export function useConfirm(): [(options: ConfirmOptions) => Promise<boolean>, React.ReactNode] {
  const [pending, setPending] = useState<Pending | null>(null);
  const confirm = useCallback(
    (options: ConfirmOptions) => new Promise<boolean>((resolve) => setPending({ ...options, resolve })),
    []
  );
  const done = useCallback(
    (confirmed: boolean) => {
      pending?.resolve(confirmed);
      setPending(null);
    },
    [pending]
  );
  return [confirm, pending ? <ConfirmDialog pending={pending} onDone={done} /> : null];
}
