"use client";

import React, { useEffect, useState } from "react";
import { SlidersHorizontal } from "lucide-react";
import { Field, Select } from "../ui/Field";
import { SchemaSettingsForm } from "./SchemaSettingsForm";
import { defaultAgentControl } from "./DefaultAgentPicker";
import { humanize } from "../../lib/settings-labels";
import type { Team } from "../../lib/teams";

interface SchemaField {
  policy: string;
}

// A team's default agent applies to its members who belong to no other team.
const CONTROLS = { default_agent: defaultAgentControl("Follow the organization's default") };

/**
 * A team's defaults: organization defaults the team sets for the turns of its own agents,
 * between the organization's value and a member's own choice. One area at a time, so a team's
 * detail stays short; keys the organization locked are shown locked.
 */
export const TeamDefaultsPanel: React.FC<{ team: Team }> = ({ team }) => {
  const [domains, setDomains] = useState<string[]>([]);
  const [domain, setDomain] = useState("");

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const schema = await fetch("/v1/settings-schema");
        const body = (schema.ok ? await schema.json() : { domains: {} }) as { domains?: Record<string, SchemaField[]> };
        // Only organization defaults can reach a team; of those, the API says which a team sets.
        const candidates = Object.entries(body.domains ?? {})
          .filter(([, fields]) => fields.some((f) => f.policy === "org_default"))
          .map(([name]) => name);
        const views = await Promise.all(
          candidates.map(async (name) => {
            const res = await fetch(`/v1/teams/${encodeURIComponent(team.id)}/settings/${name}`);
            const view = (res.ok ? await res.json() : {}) as { writable?: string[]; locked_by_org?: string[] };
            return (view.writable?.length ?? 0) + (view.locked_by_org?.length ?? 0) > 0 ? name : null;
          }),
        );
        if (cancelled) return;
        const found = views.filter((name): name is string => !!name);
        setDomains(found);
        setDomain((current) => current || (found.includes("user_preferences") ? "user_preferences" : (found[0] ?? "")));
      } catch {
        if (!cancelled) setDomains([]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [team.id]);

  if (!team.can_manage || domains.length === 0) return null;
  return (
    <section aria-labelledby={`team-defaults-${team.id}`} className="space-y-3" data-testid="team-defaults">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h3 id={`team-defaults-${team.id}`} className="flex items-center gap-1.5 text-xs font-semibold text-foreground">
            <SlidersHorizontal className="h-3.5 w-3.5 text-brand" aria-hidden="true" /> Team defaults
          </h3>
          <p className="text-[11px] text-neutral-600 dark:text-neutral-400">
            Used in the turns of the team&apos;s agents, above the organization&apos;s value; a member&apos;s own choice still wins and the organization&apos;s locks still hold. The default agent is the one new sessions start with, for members of no other team.
          </p>
        </div>
        <Field label="Settings area">
          {(control) => (
            <Select {...control} data-testid="team-defaults-domain" value={domain} onChange={(e) => setDomain(e.target.value)} className="w-48">
              {domains.map((name) => (
                <option key={name} value={name}>
                  {humanize(name)}
                </option>
              ))}
            </Select>
          )}
        </Field>
      </div>
      {domain && (
        <SchemaSettingsForm
          key={domain}
          domain={domain}
          mode="team"
          team={{ id: team.id, name: team.name, canManage: team.can_manage }}
          idScope={`team-${team.id}`}
          title={`${humanize(domain)} for ${team.name}`}
          controls={CONTROLS}
        />
      )}
    </section>
  );
};
