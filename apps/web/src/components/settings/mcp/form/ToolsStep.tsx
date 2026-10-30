"use client";

import React from "react";
import { AlertCircle, CheckCircle2, RefreshCw } from "lucide-react";
import { Disclosure } from "../../../ui/Disclosure";
import { GuidanceEditor } from "../GuidanceEditor";
import type { McpServerFormState, ValidationResult } from "./useMcpServerForm";

function headline(result: ValidationResult): string {
  if (result.valid) return "Connection Verified";
  return result.sign_in ? "Sign-in required" : "Validation Failed";
}

const ValidationFeedback: React.FC<{ result: ValidationResult }> = ({ result }) => (
  <div
    data-testid="mcp-validation"
    role="status"
    className={`flex items-start gap-2.5 rounded-lg border p-3 text-xs ${
      result.valid
        ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
        : "border-rose-500/40 bg-rose-500/10 text-rose-700 dark:text-rose-300"
    }`}
  >
    {result.valid ? (
      <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
    ) : (
      <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
    )}
    <div>
      <div className="font-semibold">{headline(result)}</div>
      <div className="mt-0.5 text-[11px] opacity-90">
        {result.message || result.error || `Discovered ${result.tools_count ?? result.tools?.length ?? 0} active tools.`}
      </div>
    </div>
  </div>
);

/** Step 4: probe the server for its tools, and the guidance the agent gets with them. */
export const ToolsStep: React.FC<{ form: McpServerFormState }> = ({ form }) => (
  <>
    <button
      type="button"
      data-testid="mcp-test-connection"
      onClick={() => void form.validate()}
      disabled={form.validating || !form.endpoint || (form.isCommand && form.commandBlocker !== null)}
      className="flex items-center gap-1.5 rounded-lg border border-border bg-surface-elevated px-3 py-1.5 text-xs text-foreground transition-all hover:bg-surface-elevated/80 disabled:opacity-50"
    >
      <RefreshCw className={`h-3.5 w-3.5 ${form.validating ? "animate-spin text-cyan" : ""}`} aria-hidden="true" />
      <span>{form.validating ? "Probing…" : "Test the connection"}</span>
    </button>
    {form.validation && <ValidationFeedback result={form.validation} />}
    <Disclosure
      // Remounted open when the server's instructions arrive, so they are not missed.
      key={form.instructionsOffered ? "offered" : "initial"}
      summary={form.guidance.trim() ? "Guidance for the agent" : "Guidance for the agent (optional)"}
      testId="mcp-guidance-toggle"
      defaultOpen={Boolean(form.guidance.trim())}
    >
      <GuidanceEditor value={form.guidance} notice={form.guidanceNotice} onChange={form.editGuidance} />
    </Disclosure>
  </>
);
