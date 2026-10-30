"use client";

import React, { useState } from "react";
import { ChevronRight, Pin } from "lucide-react";
import { Field, Textarea } from "../../ui/Field";
import { ToolLoadBadge, ToolLoadSummary } from "../../settings/ToolLoadState";
import { useToolLoadingPreview } from "../../../lib/useToolLoadingPreview";
import { mcpToolName } from "../useAgentRegistries";
import type { SectionProps } from "./types";

const CLASSES: { key: string; label: string; hint: string }[] = [
  { key: "read", label: "Read", hint: "Change nothing; allowed even in plan mode" },
  { key: "edit", label: "Edit", hint: "Change files in the workspace" },
  { key: "command", label: "Commands", hint: "Run programs in the sandbox" },
  { key: "publish", label: "Publish", hint: "Push, open pull requests, act on GitHub" },
];

function toggle(list: string[], item: string): string[] {
  return list.includes(item) ? list.filter((value) => value !== item) : [...list, item];
}

/** Which tools, MCP servers, skills and knowledge bases the agent may use. */
export const ToolsSection: React.FC<SectionProps> = ({ draft, patch, canEdit, registries }) => {
  const [advanced, setAdvanced] = useState(false);
  const allowAll = draft.whitelisted_tools.includes("*");
  const mcpTools = registries.mcpServers.flatMap((s) => (s.tools ?? []).map((t) => mcpToolName(s.name, t.name)));
  const everyTool = [...registries.tools.map((t) => t.name), ...mcpTools];
  const allowed = allowAll ? everyTool : draft.whitelisted_tools;
  const { preview, deferred, failed } = useToolLoadingPreview({
    activeTools: draft.active_tools,
    toolNames: allowed,
  });
  const core = new Set(preview?.core ?? []);

  const toggleTool = (name: string) => {
    if (!canEdit) return;
    patch({ whitelisted_tools: allowAll ? everyTool.filter((t) => t !== name) : toggle(draft.whitelisted_tools, name) });
  };

  const renderTool = (name: string, description: string) => {
    const checked = allowAll || draft.whitelisted_tools.includes(name);
    return (
      <label
        key={name}
        data-testid={`agent-tool-${name}`}
        className={`flex cursor-pointer items-start gap-2 rounded-lg border p-2 text-xs ${checked ? "border-brand/40 bg-brand/5" : "border-border/60"}`}
      >
        <input type="checkbox" className="mt-0.5" disabled={!canEdit} checked={checked} onChange={() => toggleTool(name)} />
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-1">
            <span className="truncate font-medium text-foreground">{name}</span>
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
          {description && <span className="block text-[10px] leading-tight text-neutral-500">{description}</span>}
        </span>
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

      {CLASSES.map((group) => {
        const tools = registries.tools.filter((t) => (t.permission || "command") === group.key);
        if (tools.length === 0) return null;
        return (
          <section key={group.key} aria-label={`${group.label} tools`} className="space-y-2">
            <h4 className="text-xs font-semibold text-foreground">
              {group.label} <span className="font-normal text-neutral-500">· {group.hint}</span>
            </h4>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">{tools.map((t) => renderTool(t.name, t.description))}</div>
          </section>
        );
      })}

      {registries.mcpServers.length > 0 && (
        <section aria-label="MCP servers" className="space-y-2">
          <h4 className="text-xs font-semibold text-foreground">MCP servers</h4>
          {registries.mcpServers.map((server) => (
            <div key={server.id} className="space-y-2 rounded-lg border border-border/70 p-3">
              <p className="text-xs font-medium text-foreground">{server.name}</p>
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                {(server.tools ?? []).map((t) => renderTool(mcpToolName(server.name, t.name), t.description ?? ""))}
              </div>
            </div>
          ))}
        </section>
      )}

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
