"use client";

import React, { useEffect, useId, useRef } from "react";
import { X } from "lucide-react";
import { cn } from "./cn";

interface ModalProps {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
  /** Extra classes for the panel, e.g. a wider max width. */
  className?: string;
  testId?: string;
}

/** A dialog over the page: Escape and the backdrop close it, focus moves into it and the page behind does not scroll. */
export const Modal: React.FC<ModalProps> = ({ title, onClose, children, className, testId }) => {
  const titleId = useId();
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const previouslyFocused = document.activeElement as HTMLElement | null;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    panelRef.current?.focus();
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = overflow;
      previouslyFocused?.focus?.();
    };
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/60 backdrop-blur-sm sm:items-center sm:p-4"
      onMouseDown={(event) => event.target === event.currentTarget && onClose()}
    >
      <div
        ref={panelRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        data-testid={testId}
        className={cn(
          "flex max-h-[92dvh] w-full flex-col overflow-hidden rounded-t-2xl border border-surface-border bg-surface-base shadow-2xl focus:outline-none sm:max-w-3xl sm:rounded-2xl",
          className
        )}
      >
        <div className="flex items-center justify-between border-b border-surface-border px-5 py-3">
          <h2 id={titleId} className="text-sm font-semibold text-foreground">
            {title}
          </h2>
          <button
            type="button"
            aria-label="Close"
            onClick={onClose}
            className="rounded-md p-1 text-neutral-500 hover:bg-surface-elevated hover:text-foreground"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="space-y-4 overflow-y-auto px-5 py-4">{children}</div>
      </div>
    </div>
  );
};
