"use client";

import React, { useState } from "react";
import { ChevronRight, Pin, Zap } from "lucide-react";
import { Field, Input, Textarea } from "../../ui/Field";
import { Button } from "../../ui/Button";
import { ToolLoadBadge, ToolLoadSummary } from "../../settings/ToolLoadState";
import { useToolLoadingPreview } from "../../../lib/useToolLoadingPreview";
import { buildToolGroups, matchesQuery, ToolGroupSection, type PickableTool } from "./ToolGroups";
import type { SectionProps } from "./types";

const PERMISSION_LABELS: Record<string, { label: string; hint: string }> = {
  edit: { label: "Edit", hint: "Changes files in the workspace" },
  command: { label: "Command", hint: "Runs programs in the sandbox" },
  publish: { label: "Publish", hint: "Pushes, opens pull requests, acts on GitHub" },
};

function toggle(list: string[], item: string): string[] {
  return list.includes(item) ? list.filter((value) => value !== item) : [...list, item];
}

/** Which tools, MCP servers, skills and knowledge bases the agent may use. */
export const ToolsSection: React.FC<SectionProps> = ({ draft, patch, canEdit, registries }) => {
  const [advanced, setAdvanced] = useState(false);
  const [query, setQuery] = useState("");
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  // While filtering, every group with a match is open unless the person closes it.
  const [closedWhileFiltering, setClosedWhileFiltering] = useState<Set<string>>(() => new Set());
  const allowAll = draft.whitelisted_tools.includes("*");
  const groups = buildToolGroups(registries);
  const everyTool = groups.flatMap((g) => g.tools.map((t) => t.name));
  const allowed = allowAll ? everyTool : draft.whitelisted_tools;
  const { preview, deferred, failed } = useToolLoadingPreview({
    activeTools: draft.active_tools,
    toolNames: allowed,
  });
  const core = new Set(preview?.core ?? []);
  const coreTools = registries.tools.filter((t) => t.core).map((t) => t.name);
  const isSelected = (name: string) => allowAll || draft.whitelisted_tools.includes(name);
  const selectedCount = everyTool.filter(isSelected).length;
  const missingCore = coreTools.filter((name) => !isSelected(name));
  const filtering = query.trim() !== "";

  const toggleTool = (name: string) => {
    if (!canEdit) return;
    patch({ whitelisted_tools: allowAll ? everyTool.filter((t) => t !== name) : toggle(draft.whitelisted_tools, name) });
  };

  const setTools = (names: string[], on: boolean) => {
    if (!canEdit) return;
    if (allowAll) {
      if (!on) patch({ whitelisted_tools: everyTool.filter((t) => !names.includes(t)) });
      return;
    }
    const kept = draft.whitelisted_tools.filter((t) => !names.includes(t));
    patch({ whitelisted_tools: on ? [...kept, ...names] : kept });
  };

  const flip = (set: Set<string>, key: string) => {
    const next = new Set(set);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    return next;
  };

  const renderTool = ({ name, description, permission }: PickableTool) => {
    const checked = isSelected(name);
    const approval = permission ? PERMISSION_LABELS[permission] : undefined;
    const hintId = `agent-tool-hint-${name}`;
    return (
      <label
        key={name}
        data-testid={`agent-tool-${name}`}
        className={`group relative flex cursor-pointer items-center gap-2 rounded-md px-1.5 py-1 text-xs hover:bg-surface-subnav/60 ${checked ? "bg-brand/5" : ""}`}
      >
        <input
          type="checkbox"
          disabled={!canEdit}
          checked={checked}
          onChange={() => toggleTool(name)}
          aria-describedby={description ? hintId : undefined}
        />
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-1">
            <span className={`truncate ${checked ? "font-medium text-foreground" : "text-foreground/90"}`}>{name}</span>
            {approval && (
              <span title={approval.hint} className="shrink-0 text-[10px] text-neutral-600 dark:text-neutral-400">
                · {approval.label}
              </span>
            )}
            {checked && preview && <ToolLoadBadge deferred={deferred.has(name)} core={core.has(name)} />}
            {checked && advanced && preview && !core.has(name) && (
              <button
                type="button"
                data-testid={`agent-pin-${name}`}
                aria-pressed={draft.active_tools.includes(name)}
                aria-label={draft.active_tools.includes(name) ? `Stop keeping ${name} active` : `Keep ${name} active`}
                disabled={!canEdit}
                onClick={(e) => {
                  e.preventDefault();
                  patch({ active_tools: toggle(draft.active_tools, name) });
                }}
                className={`p-0.5 ${draft.active_tools.includes(name) ? "text-amber-500" : "text-neutral-500"}`}
              >
                <Pin className={`h-3 w-3 ${draft.active_tools.includes(name) ? "fill-current" : ""}`} aria-hidden="true" />
              </button>
            )}
          </span>
        </span>
        {description && (
          // The description stays out of the list so a group reads as names; it shows on hover
          // and on keyboard focus, and screen readers get it through aria-describedby.
          <span
            id={hintId}
            role="tooltip"
            className="pointer-events-none absolute left-6 top-full z-20 mt-0.5 hidden w-72 max-w-[80vw] rounded-md border border-border bg-surface-elevated px-2 py-1.5 text-[11px] leading-snug text-foreground shadow-lg group-focus-within:block group-hover:block"
          >
            {description}
          </span>
        )}
      </label>
    );
  };

  return (
    <div className="space-y-5">
      <label className="flex items-center gap-2 text-xs text-foreground">
        <input
          type="checkbox"
          data-testid="agent-allow-all"
          disabled={!canEdit}
          checked={allowAll}
          onChange={() => patch({ whitelisted_tools: allowAll ? ["file_read", "grep_search"] : ["*"] })}
        />
        Every tool, including ones added later
      </label>
      <ToolLoadSummary preview={preview} failed={failed} />

      <div className="flex flex-wrap items-end gap-3">
        <Field label="Filter tools" className="min-w-[12rem] flex-1">
          {(control) => (
            <Input
              {...control}
              type="search"
              data-testid="agent-tool-filter"
              placeholder="Name or description"
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                setClosedWhileFiltering(new Set());
              }}
            />
          )}
        </Field>
        <Button
          size="sm"
          data-testid="agent-add-core"
          disabled={!canEdit || missingCore.length === 0}
          title={allowAll ? "Every tool is already allowed" : "The tools sent with every step of every agent"}
          onClick={() => setTools(missingCore, true)}
        >
          <Zap className="h-3.5 w-3.5" aria-hidden="true" />
          {missingCore.length === 0 ? "Core tools added" : `Add core tools (${missingCore.length})`}
        </Button>
      </div>
      <p data-testid="agent-tool-selected-count" aria-live="polite" className="text-xs text-neutral-600 dark:text-neutral-400">
        {selectedCount} of {everyTool.length} tools selected
      </p>

      <div className="space-y-2">
        {groups.map((group) => {
          const visible = group.tools.filter((t) => matchesQuery(t, query));
          if (filtering && visible.length === 0) return null;
          const open = filtering ? !closedWhileFiltering.has(group.key) : expanded.has(group.key);
          const names = group.tools.map((t) => t.name);
          const groupSelected = names.filter(isSelected).length;
          return (
            <ToolGroupSection
              key={group.key}
              group={group}
              visible={visible}
              selectedCount={groupSelected}
              open={open}
              canEdit={canEdit}
              onToggleOpen={() =>
                filtering
                  ? setClosedWhileFiltering((set) => flip(set, group.key))
                  : setExpanded((set) => flip(set, group.key))
              }
              onToggleAll={() => setTools(names, groupSelected < names.length)}
              renderTool={renderTool}
            />
          );
        })}
        {filtering && groups.every((g) => !g.tools.some((t) => matchesQuery(t, query))) && (
          <p className="text-xs text-neutral-500 dark:text-neutral-400">No tool matches “{query.trim()}”.</p>
        )}
      </div>

      {draft.kind === "agent" && (
        <>
          <section aria-label="Skills" className="space-y-2">
            <h4 className="text-xs font-semibold text-foreground">Skills</h4>
            {registries.skills.length === 0 && <p className="text-xs text-neutral-500">No skills yet.</p>}
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              {registries.skills.map((skill) => (
                <label key={skill.id} className="flex items-center gap-2 rounded-lg border border-border/60 p-2 text-xs">
                  <input
                    type="checkbox"
                    disabled={!canEdit}
                    checked={draft.skill_ids.includes(skill.id)}
                    onChange={() => patch({ skill_ids: toggle(draft.skill_ids, skill.id) })}
                  />
                  {skill.title || skill.name}
                </label>
              ))}
            </div>
          </section>
          <section aria-label="Knowledge bases" className="space-y-2">
            <h4 className="text-xs font-semibold text-foreground">Knowledge bases</h4>
            {registries.knowledgeBases.length === 0 && <p className="text-xs text-neutral-500">No knowledge bases yet.</p>}
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              {registries.knowledgeBases.map((kb) => (
                <label key={kb.id} className="flex items-center gap-2 rounded-lg border border-border/60 p-2 text-xs">
                  <input
                    type="checkbox"
                    disabled={!canEdit}
                    checked={draft.knowledge_base_ids.includes(kb.id)}
                    onChange={() => patch({ knowledge_base_ids: toggle(draft.knowledge_base_ids, kb.id) })}
                  />
                  {kb.name}
                </label>
              ))}
            </div>
          </section>
        </>
      )}

      <button
        type="button"
        aria-expanded={advanced}
        onClick={() => setAdvanced((value) => !value)}
        className="flex items-center gap-1 text-xs text-neutral-500 hover:text-foreground"
      >
        <ChevronRight className={`h-3.5 w-3.5 transition-transform ${advanced ? "rotate-90" : ""}`} aria-hidden="true" />
        Advanced: tools kept active
      </button>
      {advanced && (
        <Field
          label="Kept active"
          hint="Core tools go with every step; other allowed tools are listed by name and loaded on demand. Tools named here (or patterns such as mcp__signoz*__*search*) go with every step too."
        >
          {(control) => (
            <Textarea
              {...control}
              data-testid="agent-active-tools"
              disabled={!canEdit}
              rows={3}
              value={draft.active_tools.join("\n")}
              onChange={(e) =>
                patch({ active_tools: e.target.value.split("\n").map((line) => line.trim()).filter(Boolean) })
              }
              className="font-mono text-xs"
            />
          )}
        </Field>
      )}
    </div>
  );
};
