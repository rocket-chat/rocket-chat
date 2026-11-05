"use client";

import React, { useEffect, useId, useRef } from "react";
import { ChevronRight } from "lucide-react";
import { cn } from "../../ui/cn";
import { mcpToolName, type AgentRegistries } from "../useAgentRegistries";

export interface PickableTool {
  name: string;
  description: string;
  /** read, edit, command or publish; unknown for an MCP server's tools. */
  permission?: string;
}

export interface ToolGroup {
  key: string;
  title: string;
  /** Says what the group is in the accessible names of its controls ("Git", "GitHub MCP"). */
  label: string;
  mcp: boolean;
  tools: PickableTool[];
}

/** One group per built-in domain or toolset the catalogue names, then one per MCP server. */
export function buildToolGroups(registries: AgentRegistries): ToolGroup[] {
  const byCategory = new Map<string, PickableTool[]>();
  for (const tool of registries.tools) {
    const list = byCategory.get(tool.category) ?? [];
    list.push({ name: tool.name, description: tool.description, permission: tool.permission });
    byCategory.set(tool.category, list);
  }
  const builtIn = [...byCategory.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([category, tools]) => ({ key: `tools-${category}`, title: category, label: category, mcp: false, tools }));
  const servers = registries.mcpServers.map((server) => ({
    key: `mcp-${server.id}`,
    title: server.name,
    label: `${server.name} MCP`,
    mcp: true,
    tools: (server.tools ?? []).map((tool) => ({
      name: mcpToolName(server.name, tool.name),
      description: tool.description ?? "",
    })),
  }));
  return [...builtIn, ...servers];
}

export function matchesQuery(tool: PickableTool, query: string): boolean {
  const needle = query.trim().toLowerCase();
  return !needle || tool.name.toLowerCase().includes(needle) || tool.description.toLowerCase().includes(needle);
}

/** A stable, readable test id fragment ("Conversation & planning" -> "conversation-planning"). */
export function groupSlug(group: ToolGroup): string {
  const base = group.mcp ? `mcp-${group.title}` : group.title;
  return base.replace(/[^a-zA-Z0-9]+/g, "-").replace(/^-+|-+$/g, "").toLowerCase();
}

/** A checkbox that also shows "some" (the native indeterminate state, announced as mixed). */
const TriStateCheckbox: React.FC<{
  checked: boolean;
  indeterminate: boolean;
  disabled: boolean;
  label: string;
  testId: string;
  onChange: () => void;
}> = ({ checked, indeterminate, disabled, label, testId, onChange }) => {
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (ref.current) ref.current.indeterminate = indeterminate;
  }, [indeterminate]);
  return (
    <input
      ref={ref}
      type="checkbox"
      aria-label={label}
      data-testid={testId}
      disabled={disabled}
      checked={checked}
      onChange={onChange}
      className="h-3.5 w-3.5 shrink-0"
    />
  );
};

interface ToolGroupSectionProps {
  group: ToolGroup;
  /** The tools of the group shown now: all of them, or those matching the filter. */
  visible: PickableTool[];
  selectedCount: number;
  open: boolean;
  canEdit: boolean;
  onToggleOpen: () => void;
  /** Adds every tool of the group, or removes them all when every one is already selected. */
  onToggleAll: () => void;
  renderTool: (tool: PickableTool) => React.ReactNode;
}

/** A collapsible group: its header says how many of its tools are selected and takes them all. */
export const ToolGroupSection: React.FC<ToolGroupSectionProps> = ({
  group,
  visible,
  selectedCount,
  open,
  canEdit,
  onToggleOpen,
  onToggleAll,
  renderTool,
}) => {
  const bodyId = useId();
  const slug = groupSlug(group);
  const total = group.tools.length;
  const all = total > 0 && selectedCount === total;
  return (
    <section
      aria-label={`${group.label} tools`}
      data-testid={`agent-tool-group-${slug}`}
      className={cn("rounded-lg border", selectedCount > 0 ? "border-brand/30" : "border-border/60")}
    >
      <div className="flex items-center gap-2 px-2.5 py-1.5">
        <TriStateCheckbox
          checked={all}
          indeterminate={selectedCount > 0 && !all}
          disabled={!canEdit || total === 0}
          label={all ? `Remove all ${group.label} tools` : `Add all ${group.label} tools`}
          testId={`agent-tool-group-toggle-${slug}`}
          onChange={onToggleAll}
        />
        <button
          type="button"
          aria-expanded={open}
          aria-controls={bodyId}
          data-testid={`agent-tool-group-header-${slug}`}
          onClick={onToggleOpen}
          className="flex min-w-0 flex-1 cursor-pointer items-center gap-1.5 rounded-md py-0.5 text-left text-xs font-semibold text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/60"
        >
          <ChevronRight className={cn("h-3.5 w-3.5 shrink-0 transition-transform", open && "rotate-90")} aria-hidden="true" />
          <span className="truncate">{group.title}</span>
          {group.mcp && (
            <span className="rounded border border-border/70 px-1 text-[10px] font-medium text-neutral-500 dark:text-neutral-400">
              MCP
            </span>
          )}
          <span
            data-testid={`agent-tool-group-count-${slug}`}
            className="ml-auto shrink-0 font-normal tabular-nums text-neutral-600 dark:text-neutral-400"
          >
            {selectedCount} / {total}
          </span>
        </button>
      </div>
      <div id={bodyId} hidden={!open}>
        {open && (
          <div className="grid grid-cols-1 gap-x-3 px-1.5 pb-1.5 sm:grid-cols-2 lg:grid-cols-3">
            {visible.length === 0 ? (
              <p className="text-xs text-neutral-500 dark:text-neutral-400">No tools discovered yet.</p>
            ) : (
              visible.map(renderTool)
            )}
          </div>
        )}
      </div>
    </section>
  );
};
