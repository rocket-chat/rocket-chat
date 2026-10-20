"use client";

import React, { useState, useRef, useEffect } from "react";
import { ShieldCheck, Info } from "lucide-react";
import { useMissionStore } from "../lib/store";
import { PermissionMode } from "../types/mission";

export interface PermissionModeOption {
  value: PermissionMode;
  label: string;
  hint: string;
  edits: string;
  commands: string;
  publishing: string;
  summary: string;
}

export const PERMISSION_MODE_OPTIONS: PermissionModeOption[] = [
  {
    value: "plan",
    label: "Read-only",
    hint: "Read-only: The agent inspects code and creates plans without modifying files or running commands.",
    edits: "Blocked",
    commands: "Blocked",
    publishing: "Blocked",
    summary: "Safe exploration and planning without touching workspace code.",
  },
  {
    value: "ask",
    label: "Ask first",
    hint: "Strict supervision: Every file edit, shell command, and git publish requires your explicit approval.",
    edits: "Requires Approval",
    commands: "Requires Approval",
    publishing: "Requires Approval",
    summary: "Maximum human verification for every non-read operation.",
  },
  {
    value: "auto_edit",
    label: "Auto-edit",
    hint: "Semi-autonomous: File edits run freely, but shell commands and git publications require your approval.",
    edits: "Autonomous",
    commands: "Requires Approval",
    publishing: "Requires Approval",
    summary: "Code generation runs freely; terminal execution and git are gated.",
  },
  {
    value: "auto",
    label: "Auto",
    hint: "Edits, commands and publishing run freely; irreversible actions (merging, deleting on GitHub, a hard revert, wiping the workspace) still wait for you.",
    edits: "Autonomous",
    commands: "Autonomous",
    publishing: "Critical Gated",
    summary: "Smooth development; only irreversible actions pause.",
  },
  {
    value: "bypass",
    label: "No approvals",
    hint: "Nothing waits for approval, not even irreversible actions. Needs 'Allow bypass permissions' in Settings > Security & data.",
    edits: "Autonomous",
    commands: "Autonomous",
    publishing: "Autonomous",
    summary: "Hands-off overnight execution without any approval pauses.",
  },
];

// How much can happen without you, from careful (green) to unattended (red).
const MODE_DOT: Record<string, string> = {
  plan: "bg-emerald-500",
  ask: "bg-emerald-500",
  auto_edit: "bg-amber-500",
  auto: "bg-orange-500",
  bypass: "bg-rose-500",
};

/** Lets the member choose how closely this session's work is supervised. */
export const PermissionModePicker: React.FC<{ disabled?: boolean; bare?: boolean }> = ({ disabled, bare }) => {
  const permissionMode = useMissionStore((s) => s.permissionMode);
  const setPermissionMode = useMissionStore((s) => s.setPermissionMode);
  const selected = PERMISSION_MODE_OPTIONS.find((o) => o.value === permissionMode);
  const [showTooltip, setShowTooltip] = useState(false);
  const tooltipRef = useRef<HTMLDivElement>(null);

  // Close tooltip on outside click
  useEffect(() => {
    if (!showTooltip) return;
    const handleOutside = (e: MouseEvent) => {
      if (tooltipRef.current && !tooltipRef.current.contains(e.target as Node)) {
        setShowTooltip(false);
      }
    };
    document.addEventListener("mousedown", handleOutside);
    return () => document.removeEventListener("mousedown", handleOutside);
  }, [showTooltip]);

  return (
    <div className="relative inline-flex items-center">
      <label
        className={`flex items-center gap-1.5 px-2.5 py-1.5 text-muted-foreground transition-colors ${
          bare ? "rounded-l-xl focus-within:bg-elevated" : "rounded-lg bg-surface border border-border focus-within:border-primary/60"
        }`}
        title={selected ? `${selected.label}: ${selected.hint}` : "Supervision follows your organization's default."}
      >
        <ShieldCheck className="w-3.5 h-3.5" aria-hidden="true" />
        <span className={`h-1.5 w-1.5 rounded-full ${MODE_DOT[permissionMode] ?? "bg-neutral-400"}`} aria-hidden="true" />
        <span className="sr-only">Permission mode</span>
        <select
          data-testid="permission-mode"
          aria-label="Permission mode"
          value={permissionMode}
          disabled={disabled}
          onChange={(e) => e.target.value && setPermissionMode(e.target.value as PermissionMode)}
          className="bg-transparent text-xs text-foreground focus:outline-none cursor-pointer"
        >
          <option value="" title="Supervision follows organization default">
            Org default
          </option>
          {PERMISSION_MODE_OPTIONS.map((option) => (
            <option key={option.value} value={option.value} title={option.hint}>
              {option.label}
            </option>
          ))}
        </select>
      </label>

      {/* Tooltip trigger */}
      <button
        type="button"
        aria-label="Permission modes explanation"
        onClick={() => setShowTooltip((v) => !v)}
        onMouseEnter={() => setShowTooltip(true)}
        className="ml-1 p-1 rounded hover:bg-surface-elevated text-muted-foreground hover:text-foreground transition-colors focus:outline-none"
        title="View permission modes explanation"
      >
        <Info className="w-3.5 h-3.5" />
      </button>

      {/* Detailed Tooltip Popover */}
      {showTooltip && (
        <div
          ref={tooltipRef}
          onMouseLeave={() => setShowTooltip(false)}
          className="absolute left-0 top-full mt-2 w-80 sm:w-96 p-3.5 rounded-xl bg-surface-elevated border border-border shadow-xl z-50 text-xs text-foreground backdrop-blur-md"
        >
          <div className="flex items-center justify-between pb-2 mb-2 border-b border-border">
            <span className="font-semibold text-xs tracking-wide uppercase text-muted-foreground">
              Permission Modes
            </span>
            <span className="text-[10px] text-muted-foreground">Supervision Levels</span>
          </div>

          <div className="space-y-2.5 max-h-80 overflow-y-auto pr-1">
            {PERMISSION_MODE_OPTIONS.map((opt) => {
              const isActive = opt.value === permissionMode;
              return (
                <div
                  key={opt.value}
                  className={`p-2 rounded-lg border transition-colors ${
                    isActive
                      ? "bg-primary/10 border-primary/40 text-foreground"
                      : "bg-surface/50 border-border/60 hover:bg-surface text-muted-foreground"
                  }`}
                >
                  <div className="flex items-center justify-between mb-1">
                    <span className="font-medium text-foreground flex items-center gap-1.5">
                      {opt.label}
                      {isActive && (
                        <span className="text-[9px] px-1.5 py-0.2 rounded-full bg-primary/20 text-primary font-semibold">
                          Active
                        </span>
                      )}
                    </span>
                    <span className="text-[10px] opacity-75">
                      Edits: {opt.edits}
                    </span>
                  </div>
                  <p className="text-[11px] leading-relaxed mb-1.5 text-foreground/90">
                    {opt.summary}
                  </p>
                  <div className="flex items-center gap-2 text-[10px] opacity-80 pt-1 border-t border-border/40">
                    <span>Terminal: <strong className="text-foreground">{opt.commands}</strong></span>
                    <span>•</span>
                    <span>Publish: <strong className="text-foreground">{opt.publishing}</strong></span>
                  </div>
                </div>
              );
            })}
          </div>

          <div className="mt-2.5 pt-2 border-t border-border text-[10px] text-muted-foreground leading-normal">
            💡 <strong>No approvals</strong> runs everything unattended. It needs <code className="text-foreground">Allow bypass permissions</code> enabled in Settings &gt; Security &amp; data.
          </div>
        </div>
      )}
    </div>
  );
};
