"use client";

import React, { useCallback, useEffect, useState } from "react";
import { AlertCircle, Lock } from "lucide-react";
import { DefaultAgentPicker } from "./DefaultAgentPicker";
import { InheritanceBadge } from "./InheritanceBadge";
import { useSettingsFormHandle } from "./SettingsSave";
import { fetchDefaultAgent, type DefaultAgent } from "../../lib/default-agent";
import { useTeams } from "../../lib/teams";

const FIELD_ID = "settings-field-user_preferences-default_agent";

function levelName(source: string, teamName: (id: string) => string): string {
  if (source === "member") return "your own choice";
  if (source.startsWith("team:")) return `team "${teamName(source.slice(5))}"`;
  if (source === "org") return "the organization";
  return "the platform";
}

/**
 * The member's default agent, beside what they would inherit: their only team's, else the
 * organization's. Its own form (not the schema form) because the team level depends on the
 * member's teams, which the generic settings view does not resolve.
 */
export const DefaultAgentPanel: React.FC = () => {
  const { teams } = useTeams();
  const teamName = (id: string) => teams.find((t) => t.id === id)?.name ?? id;
  const [view, setView] = useState<DefaultAgent | null>(null);
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const found = await fetchDefaultAgent();
    if (!found) return;
    setView(found);
    setValue(found.own_agent_id ?? "");
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const stored = view?.own_agent_id ?? "";
  const changes = view && value !== stored ? 1 : 0;

  const clear = async (): Promise<boolean> => {
    const res = await fetch("/v1/settings/user_preferences/default_agent", { method: "DELETE" });
    if (res.ok) await load();
    return res.ok;
  };

  const save = async (): Promise<boolean> => {
    setError(null);
    if (!value) return clear();
    const res = await fetch("/v1/settings/user_preferences", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ overrides: { default_agent: value } }),
    });
    if (!res.ok) {
      const body = (await res.json().catch(() => ({}))) as { detail?: { message?: string }[] | string };
      setError(Array.isArray(body.detail) ? (body.detail[0]?.message ?? "Rejected") : (body.detail ?? `HTTP ${res.status}`));
      return false;
    }
    await load();
    return true;
  };

  useSettingsFormHandle({ id: "default-agent", title: "Default agent", changes, save, discard: () => setValue(stored) });

  const inherited = view?.inherited;
  const inheritLabel = inherited?.agent_id
    ? `Follow ${levelName(inherited.source, teamName)}: ${inherited.agent_name || inherited.agent_id}`
    : "Follow the platform's default";

  return (
    <section className="space-y-4" data-testid="default-agent-panel">
      <div className="border-b border-surface-border pb-2">
        <h2 className="font-display text-base font-semibold text-foreground">Default agent</h2>
        <p className="mt-1 text-xs text-neutral-600 dark:text-neutral-400">
          The agent your new sessions start with: in the new-session picker, a session created through the API without
          an agent, and a Slack direct message to a bot without an agent of its own.
        </p>
      </div>
      <div
        id="setting-user_preferences-default_agent"
        className={`space-y-2 rounded-xl border bg-surface-card p-4 ${error ? "border-rose-700" : changes ? "border-brand/60" : "border-surface-border"}`}
      >
        <div className="flex flex-wrap items-center justify-between gap-2">
          <label htmlFor={FIELD_ID} className="text-xs font-medium text-foreground">
            Default agent
          </label>
          {view && (
            <span data-testid="default-agent-source">
              <InheritanceBadge
                provenance={{ source: view.source, locked_by: view.locked ? "org" : null, refused: view.skipped }}
                onReset={view.source === "member" ? () => void clear() : undefined}
              />
            </span>
          )}
        </div>
        <DefaultAgentPicker
          id={FIELD_ID}
          value={value}
          disabled={!view || view.locked}
          onChange={setValue}
          inheritLabel={inheritLabel}
          includePersonal
        />
        <p className="text-[11px] leading-snug text-neutral-600 dark:text-neutral-400" data-testid="default-agent-effective">
          {view?.agent_id
            ? `New sessions start with ${view.agent_name || view.agent_id}, from ${levelName(view.source, teamName)}.`
            : "No default applies: the cockpit keeps the agent you last chose, and other sessions use the general agent."}
          {view && !view.team_id && teams.some((t) => t.my_role) && " Your teams' defaults do not apply: you belong to more than one team."}
        </p>
        {view?.locked && (
          <p className="flex items-center gap-1.5 text-[11px] text-neutral-600 dark:text-neutral-400">
            <Lock className="h-3 w-3" aria-hidden="true" /> An administrator locked the organization&apos;s default.
          </p>
        )}
        {view && view.skipped.length > 0 && (
          <p className="flex items-center gap-1.5 text-[11px] text-amber-700 dark:text-amber-400" data-testid="default-agent-skipped">
            <AlertCircle className="h-3 w-3 shrink-0" aria-hidden="true" />
            Skipped {view.skipped.map((s) => levelName(s, teamName)).join(" and ")}: its agent is no longer available to you.
          </p>
        )}
        {error && <p className="text-[11px] text-rose-500">{error}</p>}
      </div>
    </section>
  );
};
