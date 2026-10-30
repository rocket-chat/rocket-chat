"use client";

import React, { useCallback, useEffect, useState } from "react";

interface StarterSkill {
  name: string;
  description: string;
  enabled: boolean;
}

/** The shipped instruction skills; an administrator switches each on or off in one click. */
export const StarterSkillsPanel: React.FC<{ isAdmin: boolean }> = ({ isAdmin }) => {
  const [skills, setSkills] = useState<StarterSkill[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const res = await fetch("/v1/skills/starter");
    if (res.ok) setSkills(await res.json());
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const toggle = async (name: string, on: boolean) => {
    setError(null);
    // The organization's other `general` settings are kept: the route replaces the domain.
    const current = await fetch("/v1/admin/settings/general");
    if (!current.ok) {
      setError("Could not read the organization's settings.");
      return;
    }
    const body = (await current.json()) as { config: Record<string, unknown>; locked_keys: string[] };
    const enabled = skills.filter((s) => (s.name === name ? on : s.enabled)).map((s) => s.name);
    const res = await fetch("/v1/admin/settings/general", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ config: { ...body.config, starter_skills: enabled }, locked_keys: body.locked_keys ?? [] }),
    });
    if (!res.ok) setError("Could not save the change.");
    await load();
  };

  if (skills.length === 0) return null;
  return (
    <section className="space-y-2" data-testid="starter-skills">
      <div>
        <h2 className="font-display text-base font-semibold text-foreground">Starter skills</h2>
        <p className="mt-1 text-xs text-neutral-600 dark:text-neutral-400">
          Shipped with the platform; run one with <code>/name</code> in the composer. A skill of yours with the same name replaces it.
        </p>
      </div>
      {error && (
        <p role="alert" className="text-xs text-red-700 dark:text-red-300">
          {error}
        </p>
      )}
      <ul className="divide-y divide-border rounded-lg border border-border">
        {skills.map((skill) => (
          <li key={skill.name} className="flex items-center justify-between gap-3 px-3 py-2">
            <div className="min-w-0">
              <p className="font-mono text-sm text-foreground">/{skill.name}</p>
              <p className="text-xs text-neutral-600 dark:text-neutral-400">{skill.description}</p>
            </div>
            <label className="flex shrink-0 items-center gap-2 text-xs text-foreground">
              <input
                type="checkbox"
                data-testid={`starter-skill-${skill.name}`}
                checked={skill.enabled}
                disabled={!isAdmin}
                onChange={(e) => void toggle(skill.name, e.target.checked)}
              />
              {skill.enabled ? "On" : "Off"}
            </label>
          </li>
        ))}
      </ul>
    </section>
  );
};
