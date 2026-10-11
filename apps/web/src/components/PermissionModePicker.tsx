"use client";

import React from "react";
import { ShieldCheck } from "lucide-react";
import { useMissionStore } from "../lib/store";
import { PermissionMode } from "../types/mission";

export const PERMISSION_MODE_OPTIONS: { value: PermissionMode; label: string; hint: string }[] = [
  { value: "plan", label: "Plan", hint: "Read-only: the agent investigates and proposes a plan." },
  { value: "ask", label: "Ask", hint: "Approve every edit, command and publish." },
  { value: "auto_edit", label: "Auto-edit", hint: "Edits run freely; approve commands and publish." },
  { value: "auto", label: "Auto", hint: "The agent asks when it judges an action risky." },
  { value: "bypass", label: "Bypass", hint: "Nothing waits for approval (if your organization allows it)." },
];

/** Lets the member choose how closely this session's work is supervised. */
export const PermissionModePicker: React.FC<{ disabled?: boolean }> = ({ disabled }) => {
  const permissionMode = useMissionStore((s) => s.permissionMode);
  const setPermissionMode = useMissionStore((s) => s.setPermissionMode);
  const selected = PERMISSION_MODE_OPTIONS.find((o) => o.value === permissionMode);

  return (
    <label
      className="flex items-center gap-1.5 px-2 py-1.5 rounded-lg bg-surface border border-border text-muted-foreground focus-within:border-primary/60"
      title={selected?.hint ?? "Supervision follows your organization's default."}
    >
      <ShieldCheck className="w-3.5 h-3.5" aria-hidden="true" />
      <span className="sr-only">Permission mode</span>
      <select
        data-testid="permission-mode"
        aria-label="Permission mode"
        value={permissionMode}
        disabled={disabled}
        onChange={(e) => e.target.value && setPermissionMode(e.target.value as PermissionMode)}
        className="bg-transparent text-xs text-foreground focus:outline-none cursor-pointer"
      >
        <option value="">Default</option>
        {PERMISSION_MODE_OPTIONS.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  );
};
