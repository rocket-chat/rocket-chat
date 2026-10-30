"use client";

import React from "react";
import { Plus } from "lucide-react";
import { Modal } from "../../ui/Modal";
import type { McpRunnerStatus, McpServerConfig } from "../../../types/mission";
import { AuthStep } from "./form/AuthStep";
import { ConnectionStep } from "./form/ConnectionStep";
import { PresetStep } from "./form/PresetStep";
import { ToolsStep } from "./form/ToolsStep";
import { STEP_LABELS, useMcpServerForm, type McpServerFormState, type Step } from "./form/useMcpServerForm";

interface McpServerFormProps {
  scope: "user" | "org";
  isAdmin: boolean;
  /** Whether command servers can run here; null while it is still being checked. */
  runner: McpRunnerStatus | null;
  onSaved: (server: McpServerConfig, message: string) => void;
  onCancel: () => void;
  /** Edit this registered server instead of registering a new one. */
  editing?: McpServerConfig;
}

const STEP_COMPONENTS: Record<Step, React.FC<{ form: McpServerFormState }>> = {
  preset: PresetStep,
  connection: ConnectionStep,
  auth: AuthStep,
  tools: ToolsStep,
};

function titleOf(scope: "user" | "org", editing?: McpServerConfig): string {
  if (editing) return `Edit ${editing.name}`;
  return scope === "org" ? "Register an organization MCP server" : "Register a personal MCP server";
}

const StepList: React.FC<{ form: McpServerFormState }> = ({ form }) => (
  <ol aria-label="Steps" className="flex flex-wrap gap-2 text-[11px]">
    {form.steps.map((name, index) => (
      <li key={name}>
        <button
          type="button"
          data-testid={`mcp-step-${name}`}
          aria-current={form.step === name ? "step" : undefined}
          onClick={() => form.setStep(name)}
          className={`rounded-full border px-2 py-0.5 ${form.step === name ? "border-brand bg-brand/10 text-brand" : "border-border text-neutral-500"}`}
        >
          {index + 1}. {STEP_LABELS[name]}
        </button>
      </li>
    ))}
  </ol>
);

const Footer: React.FC<{ form: McpServerFormState; onCancel: () => void }> = ({ form, onCancel }) => {
  const { steps, stepIndex, editing, signsIn } = form;
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border/60 pt-3">
      <span className="flex items-center gap-2">
        {stepIndex > 0 && (
          <button
            type="button"
            onClick={() => form.setStep(steps[stepIndex - 1])}
            className="rounded-lg border border-border px-3 py-1.5 text-xs text-foreground"
          >
            Back
          </button>
        )}
        {stepIndex < steps.length - 1 && (
          <button
            type="button"
            data-testid="mcp-next"
            onClick={() => form.setStep(steps[stepIndex + 1])}
            className="rounded-lg border border-border px-3 py-1.5 text-xs text-foreground"
          >
            Next
          </button>
        )}
      </span>
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={onCancel}
          className="rounded-lg border border-border px-3 py-1.5 text-xs text-neutral-600 hover:text-foreground dark:text-neutral-400"
        >
          CANCEL
        </button>
        <button
          type="button"
          data-testid="mcp-save"
          onClick={() => void form.save()}
          disabled={form.isCommand && form.commandBlocker !== null}
          className="flex items-center gap-1.5 rounded-lg border border-cyan/40 bg-cyan px-4 py-1.5 text-xs font-semibold text-slate-950 shadow-sm transition-all hover:bg-cyan/90 disabled:opacity-50"
        >
          <Plus className="h-3.5 w-3.5" aria-hidden="true" />
          <span data-testid="mcp-save-label">{signsIn ? (editing ? "Save & sign in" : "Add & sign in") : "Probe & connect server"}</span>
        </button>
      </div>
    </div>
  );
};

/** Registers a new MCP server, or edits a registered one, as a wizard: a preset (new servers
 * only), the connection, its sign-in or variables, then the tools it offers and the guidance. */
export const McpServerForm: React.FC<McpServerFormProps> = ({ scope, isAdmin, runner, onSaved, onCancel, editing }) => {
  const form = useMcpServerForm({ scope, isAdmin, runner, editing, onSaved });
  const CurrentStep = STEP_COMPONENTS[form.step];
  return (
    <Modal title={titleOf(scope, editing)} onClose={onCancel} testId="mcp-server-modal">
      <CurrentStep form={form} />
      {form.formError && (
        <p role="alert" data-testid="mcp-form-error" className="text-xs text-rose-600 dark:text-rose-400">
          {form.formError}
        </p>
      )}
      <StepList form={form} />
      <Footer form={form} onCancel={onCancel} />
    </Modal>
  );
};
