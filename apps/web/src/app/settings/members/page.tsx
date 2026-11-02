"use client";

import React, { useEffect, useState } from "react";
import { UserCog, Users } from "lucide-react";
import { SettingsPage } from "../../../components/settings/SettingsPage";
import { MembersPanel } from "../../../components/settings/MembersPanel";
import { TeamsPanel } from "../../../components/settings/TeamsPanel";
import { useAccess } from "../../../lib/access";
import { useOwnerFilter } from "../../../lib/useOwnerFilter";
import { tabKeyHandler } from "../../../lib/tab-keys";

type Tab = "members" | "teams";

export default function MembersAndTeamsPage() {
  const access = useAccess();
  const [owner] = useOwnerFilter();
  // Members and roles are the organization's administrators' alone; teams concern their members too.
  const tabs: Tab[] = access.isAdmin ? ["members", "teams"] : ["teams"];
  // The person's own choice of tab; until they make one, a team named by the owner filter opens Teams.
  const [chosen, setTab] = useState<Tab | null>(null);
  useEffect(() => setTab(null), [owner]);

  const preferred: Tab = owner.startsWith("team:") ? "teams" : tabs[0];
  const current = chosen && tabs.includes(chosen) ? chosen : preferred;
  return (
    <SettingsPage
      section="Governance"
      title="Members & teams"
      description="Who belongs to the organization and with which role, and the teams that own agents, skills, MCP servers and knowledge bases of their own."
    >
      <div className="space-y-6">
        <div role="tablist" aria-label="Members and teams" className="flex gap-1 border-b border-border/60">
          {tabs.map((t) => (
            <button
              key={t}
              type="button"
              role="tab"
              id={`members-tab-${t}`}
              data-testid={`members-tab-${t}`}
              aria-selected={current === t}
              aria-controls="members-tabpanel"
              tabIndex={current === t ? 0 : -1}
              onClick={() => setTab(t)}
              onKeyDown={tabKeyHandler(tabs, current, setTab, (next) => `members-tab-${next}`)}
              className={`-mb-px flex items-center gap-1.5 border-b-2 px-3 py-2 text-xs ${current === t ? "border-brand font-semibold text-foreground" : "border-transparent text-neutral-600 hover:text-foreground dark:text-neutral-400"}`}
            >
              {t === "members" ? <UserCog className="h-3.5 w-3.5" aria-hidden="true" /> : <Users className="h-3.5 w-3.5" aria-hidden="true" />}
              {t === "members" ? "Members" : "Teams"}
            </button>
          ))}
        </div>
        <div role="tabpanel" id="members-tabpanel" aria-labelledby={`members-tab-${current}`}>
          {current === "members" ? <MembersPanel /> : <TeamsPanel />}
        </div>
      </div>
    </SettingsPage>
  );
}
