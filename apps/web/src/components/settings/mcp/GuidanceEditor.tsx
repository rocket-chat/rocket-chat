"use client";

import React, { useId, useState } from "react";
import { Eye, Pencil, Sparkles } from "lucide-react";
import { MarkdownRenderer } from "../../MarkdownRenderer";
import { Button } from "../../ui/Button";
import { Textarea } from "../../ui/Field";
import { cn } from "../../ui/cn";
import { GUIDANCE_TEMPLATE, INLINE_GUIDANCE_CHARS, MAX_GUIDANCE_CHARS, triggerOf } from "../../../lib/mcp-guidance";

type Mode = "write" | "preview";

/**
 * Writes an MCP server's guidance in markdown, and shows what the agent will make of it: the first
 * line is the trigger it weighs the server by, the rest is given once it uses the server.
 */
export const GuidanceEditor: React.FC<{ value: string; onChange: (value: string) => void; notice?: string | null }> = ({
  value,
  onChange,
  notice,
}) => {
  const [mode, setMode] = useState<Mode>("write");
  const baseId = useId();
  const length = value.length;
  const trigger = triggerOf(value);
  const state = length > MAX_GUIDANCE_CHARS ? "over" : length > INLINE_GUIDANCE_CHARS ? "long" : "short";

  const tab = (id: Mode, label: string, icon: React.ReactNode) => (
    <button
      type="button"
      role="tab"
      id={`${baseId}-${id}`}
      aria-selected={mode === id}
      aria-controls={`${baseId}-panel`}
      data-testid={`guidance-tab-${id}`}
      onClick={() => setMode(id)}
      className={cn(
        "inline-flex cursor-pointer items-center gap-1.5 rounded-md px-2.5 py-1 text-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/60",
        mode === id ? "bg-surface-elevated font-medium text-foreground" : "text-neutral-600 hover:text-foreground dark:text-neutral-400",
      )}
    >
      {icon}
      {label}
    </button>
  );

  return (
    <div className="space-y-2" data-testid="guidance-editor">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div role="tablist" aria-label="Guidance editor" className="flex items-center gap-1 rounded-lg border border-surface-border p-0.5">
          {tab("write", "Write", <Pencil className="h-3 w-3" aria-hidden="true" />)}
          {tab("preview", "Preview", <Eye className="h-3 w-3" aria-hidden="true" />)}
        </div>
        {!value.trim() && (
          <Button size="sm" variant="ghost" data-testid="guidance-template" onClick={() => onChange(GUIDANCE_TEMPLATE)}>
            <Sparkles className="h-3.5 w-3.5" aria-hidden="true" />
            Start from a template
          </Button>
        )}
      </div>

      {notice && (
        <p role="status" data-testid="guidance-notice" className="rounded-lg border border-cyan/30 bg-cyan/10 px-3 py-2 text-xs text-foreground">
          {notice}
        </p>
      )}

      <div id={`${baseId}-panel`} role="tabpanel" aria-labelledby={`${baseId}-${mode}`}>
        {mode === "write" ? (
          <Textarea
            aria-label="Guidance for the agent (markdown)"
            data-testid="guidance-input"
            rows={9}
            spellCheck
            value={value}
            onChange={(event) => onChange(event.target.value)}
            placeholder={"Use for billing questions and refunds.\n\n## How to use\n- Always check the customer's currency first.\n\n## Avoid\n- Never run a refund without confirmation."}
            className="font-mono text-xs"
          />
        ) : (
          <div data-testid="guidance-preview" className="min-h-[9rem] rounded-lg border border-surface-border bg-surface-card p-3 text-sm">
            {value.trim() ? <MarkdownRenderer content={value} /> : <p className="text-xs text-neutral-500">Nothing to preview yet.</p>}
          </div>
        )}
      </div>

      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 text-xs">
        <p data-testid="guidance-trigger" className="min-w-0 text-neutral-600 dark:text-neutral-400">
          {trigger ? (
            <>
              <span className="font-medium text-foreground">The agent sees:</span> “{trigger}”
            </>
          ) : (
            "No guidance: this server adds nothing to the agent's context."
          )}
        </p>
        <span
          data-testid="guidance-count"
          data-state={state}
          className={cn(
            "tabular-nums",
            state === "over" ? "text-rose-600 dark:text-rose-400" : state === "long" ? "text-amber-700 dark:text-amber-400" : "text-neutral-500",
          )}
        >
          {length.toLocaleString()} / {MAX_GUIDANCE_CHARS.toLocaleString()}
        </span>
      </div>
      <p className="text-xs leading-relaxed text-neutral-500 dark:text-neutral-400">
        The first line says when to use this server and is always in the agent&apos;s instructions. The rest is given once the
        agent uses the server, and stays for the session.{" "}
        {state === "long" || state === "over"
          ? "Past 1,500 characters the agent reads it on request instead of as soon as a tool is loaded."
          : "Keep it under 1,500 characters to have it apply as soon as a tool is loaded."}
      </p>
    </div>
  );
};
