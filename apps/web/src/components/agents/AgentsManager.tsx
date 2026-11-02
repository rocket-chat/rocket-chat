"use client";

import React, { useCallback, useEffect, useState } from "react";
import { tabKeyHandler } from "../../lib/tab-keys";
import { Bot, Plus, Users } from "lucide-react";
import { useShallow } from "zustand/react/shallow";
import { useMissionStore } from "../../lib/store";
import { hasCapability, useAccess } from "../../lib/access";
import { useTeams } from "../../lib/teams";
import { useOwnerFilter } from "../../lib/useOwnerFilter";
import {
  canManageOwner,
  creatableOwners,
  defaultOwner,
  groupByOwner,
  matchesOwner,
  ownerFields,
  ownerKey,
} from "../../lib/ownership";
import type { AgentPersonaConfig, SubagentConfig } from "../../types/mission";
import { OwnerBadge, useMoveConfirmation } from "../settings/Owner";
import { AgentEditor } from "./AgentEditor";
import { blankDraft, fromAgent, fromSubagent, toAgent, toSubagent, type AgentDraft, type AgentKind } from "./draft";
import { useAgentRegistries } from "./useAgentRegistries";

async function detailOf(res: Response, fallback: string): Promise<string> {
  const body = await res.json().catch(() => ({}));
  return typeof body.detail === "string" ? body.detail : fallback;
}

/** Every agent the person may use, grouped by owner (theirs, each team's, the organization's)
 * and narrowed by the settings' owner filter; administrators also edit the subagent roles. */
export const AgentsManager: React.FC = () => {
  const access = useAccess();
  const { teams } = useTeams();
  const [owner] = useOwnerFilter();
  const [confirmMove, moveDialog] = useMoveConfirmation();
  const registries = useAgentRegistries(access.isAdmin);
  const showRoles = access.isAdmin && (owner === "all" || owner === "org");
  const { agents, setAgents, fetchAgents, draft, open } = useMissionStore(
    useShallow((s) => ({
      agents: s.availableAgents,
      setAgents: s.setAvailableAgents,
      fetchAgents: s.fetchAgents,
      draft: s.agentDraft,
      open: s.openAgentDraft,
    })),
  );
  const [kind, setKind] = useState<AgentKind>("agent");
  const [roles, setRoles] = useState<SubagentConfig[]>([]);

  const loadRoles = useCallback(async () => {
    const res = await fetch("/v1/subagents");
    if (res.ok) setRoles(await res.json());
  }, []);

  useEffect(() => {
    void fetchAgents();
    return () => open(null);
  }, [fetchAgents, open]);

  useEffect(() => {
    if (showRoles) void loadRoles();
    else setKind("agent");
  }, [loadRoles, showRoles]);

  const creatable = creatableOwners(access, teams, hasCapability(access, "create_personal_agents"));
  const startOwner = defaultOwner(owner, creatable);
  const listed: AgentDraft[] =
    kind === "subagent" ? roles.map(fromSubagent) : agents.map(fromAgent).filter((a) => matchesOwner(a, owner));
  const groups = kind === "subagent" ? [{ key: "org", label: "Subagent roles", items: listed }] : groupByOwner(listed, teams);

  const canEdit = (d: AgentDraft) =>
    access.canWrite && (d.kind === "subagent" ? access.isAdmin : d.built_in ? access.isAdmin : canManageOwner(access, teams, ownerKey(d)));

  const save = async (d: AgentDraft): Promise<string | null> => {
    if (d.kind === "subagent") {
      const res = await fetch(`/v1/subagents/${encodeURIComponent(d.id)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(toSubagent(d)),
      });
      if (!res.ok) return detailOf(res, "The role was not saved.");
      const stored = fromSubagent(await res.json());
      open(stored);
      await loadRoles();
      return null;
    }
    const copy = d.built_in && !access.isAdmin;
    const before = agents.find((a) => a.id === d.id);
    if (!copy && before && !before.built_in && !(await confirmMove(d.name, ownerKey(before), ownerKey(d)))) return null;
    const body: AgentPersonaConfig = copy
      ? { ...toAgent(d), id: `persona-${Date.now().toString(36)}`, name: `${d.name} (copy)`, built_in: false, scope: "user", team_id: null }
      : toAgent(d);
    const res = await fetch("/v1/agents", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) return detailOf(res, "The agent was not saved.");
    const stored: AgentPersonaConfig = await res.json();
    setAgents([...agents.filter((a) => a.id !== stored.id), stored]);
    open(fromAgent(stored));
    return null;
  };

  const duplicate = (d: AgentDraft) => {
    const target = d.kind === "agent" && startOwner ? ownerFields(startOwner) : { scope: d.scope, team_id: d.team_id };
    const copy = blankDraft(d.kind, target.scope, target.team_id);
    open({ ...d, id: copy.id, name: `${d.name} (copy)`, built_in: false, customized: false, scope: copy.scope, team_id: copy.team_id, user_id: null });
  };

  const reset = async (d: AgentDraft) => {
    const url = d.kind === "subagent" ? `/v1/subagents/${d.id}` : `/v1/agents/${d.id}`;
    await fetch(url, { method: "DELETE" });
    if (d.kind === "subagent") await loadRoles();
    else await fetchAgents();
    open(null);
  };

  const create = () => {
    if (kind === "subagent") {
      open(blankDraft(kind, "org"));
      return;
    }
    if (!startOwner) return;
    const target = ownerFields(startOwner);
    open(blankDraft(kind, target.scope, target.team_id));
  };

  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-[18rem_minmax(0,1fr)]">
      <div className="space-y-3">
        {showRoles && (
          <div role="tablist" aria-label="What to list" className="flex gap-1">
            {(["agent", "subagent"] as const).map((k) => (
              <button
                key={k}
                type="button"
                role="tab"
                id={`agents-tab-${k}`}
                aria-selected={kind === k}
                aria-controls="agents-tabpanel"
                tabIndex={kind === k ? 0 : -1}
                data-testid={`agents-tab-${k}`}
                onClick={() => {
                  setKind(k);
                  open(null);
                }}
                onKeyDown={tabKeyHandler<"agent" | "subagent">(["agent", "subagent"], kind, (next) => {
                  setKind(next);
                  open(null);
                }, (next) => `agents-tab-${next}`)}
                className={`flex items-center gap-1 rounded-lg px-2.5 py-1 text-xs ${kind === k ? "bg-surface-elevated font-semibold text-foreground" : "text-neutral-500"}`}
              >
                {k === "agent" ? <Bot className="h-3.5 w-3.5" aria-hidden="true" /> : <Users className="h-3.5 w-3.5" aria-hidden="true" />}
                {k === "agent" ? "Agents" : "Subagent roles"}
              </button>
            ))}
          </div>
        )}
        <button
          type="button"
          onClick={create}
          disabled={kind === "subagent" ? !access.isAdmin : !startOwner}
          data-testid="agent-new"
          className="flex w-full items-center justify-center gap-1 rounded-lg border border-dashed border-border py-2 text-xs text-foreground hover:bg-surface-elevated disabled:opacity-50"
        >
          <Plus className="h-3.5 w-3.5" aria-hidden="true" /> {kind === "subagent" ? "New role" : "New agent"}
        </button>
        <div
          id="agents-tabpanel"
          role={showRoles ? "tabpanel" : undefined}
          aria-labelledby={showRoles ? `agents-tab-${kind}` : undefined}
        >
        <ul className="space-y-3" aria-label={kind === "subagent" ? "Subagent roles" : "Agents"}>
          {listed.length === 0 && <li className="text-xs text-neutral-500">None yet.</li>}
          {groups.map((group) => (
            <li key={group.key} data-testid={`agent-group-${group.key}`}>
              {kind === "agent" && (
                <p id={`agent-group-${group.key}`} className="mb-1 px-0.5 text-[11px] font-semibold text-neutral-600 dark:text-neutral-300">
                  {group.label}
                </p>
              )}
              <ul className="space-y-1.5" aria-labelledby={kind === "agent" ? `agent-group-${group.key}` : undefined}>
          {group.items.map((item) => (
            <li key={item.id}>
              <button
                type="button"
                data-testid={`agent-item-${item.id}`}
                aria-current={draft?.id === item.id ? "true" : undefined}
                onClick={() => open(item)}
                className={`w-full rounded-lg border p-2.5 text-left text-xs transition-colors ${draft?.id === item.id ? "border-brand bg-brand/5" : "border-border/70 hover:bg-surface-elevated"} ${item.enabled ? "" : "opacity-60"}`}
              >
                <span className="flex items-center justify-between gap-1">
                  <span className={`truncate font-semibold text-foreground ${item.enabled ? "" : "line-through"}`}>{item.name}</span>
                  <span className="flex shrink-0 items-center gap-1">
                    {item.built_in && <span className="rounded border border-border px-1 text-[9px]">Built-in</span>}
                    {item.kind === "agent" && <OwnerBadge owned={item} />}
                  </span>
                </span>
                <span className="mt-0.5 block truncate text-[10px] text-neutral-500">{item.description || item.role_title}</span>
              </button>
            </li>
          ))}
              </ul>
            </li>
          ))}
        </ul>
        </div>
      </div>
      <div className="min-w-0 rounded-xl border border-surface-border bg-surface-card p-4">
        <AgentEditor
          canEdit={draft ? canEdit(draft) : false}
          isAdmin={access.isAdmin}
          stored={!!draft && listed.some((item) => item.id === draft.id)}
          registries={registries}
          onSave={save}
          onDuplicate={duplicate}
          onReset={reset}
        />
      </div>
      {moveDialog}
    </div>
  );
};
