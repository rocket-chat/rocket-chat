"use client";

import React, { useCallback, useEffect, useState } from "react";
import { useSettingsFormHandle } from "./SettingsSave";

const CAPABILITIES: { key: string; label: string; hint: string }[] = [
  { key: "create_personal_agents", label: "Create personal agents", hint: "Agents of their own" },
  { key: "bring_own_keys", label: "Bring their own keys", hint: "Provider keys of their own" },
  { key: "register_mcp", label: "Register MCP servers", hint: "Personal remote servers" },
  { key: "publish_as_bot", label: "Publish as the GitHub App", hint: "Pull requests as the bot" },
  { key: "use_bypass_mode", label: "Use bypass mode", hint: "Nothing waits for approval" },
  { key: "manage_spend_limits", label: "Manage spend limits", hint: "Create and change limits" },
];
// Owners hold every capability (an organization cannot lock its owners out of its policy).
const ROLES = ["admin", "member"] as const;

type Grid = Record<string, string[]>;

/** Which roles hold each capability (the `access` settings), as a role × capability grid. */
export const CapabilitiesPanel: React.FC = () => {
  const [grid, setGrid] = useState<Grid>({});
  const [initial, setInitial] = useState<Grid>({});
  const [locked, setLocked] = useState<string[]>([]);

  const load = useCallback(async () => {
    const res = await fetch("/v1/admin/settings/access");
    if (!res.ok) return;
    const body = (await res.json()) as { config: Grid; locked_keys: string[] };
    setGrid(body.config ?? {});
    setInitial(body.config ?? {});
    setLocked(body.locked_keys ?? []);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const changes = CAPABILITIES.filter(
    (c) => JSON.stringify([...(grid[c.key] ?? [])].sort()) !== JSON.stringify([...(initial[c.key] ?? [])].sort()),
  ).length;

  const save = async (): Promise<boolean> => {
    const config = Object.fromEntries(
      CAPABILITIES.map((c) => [c.key, ["owner", ...(grid[c.key] ?? []).filter((r) => r !== "owner")]]),
    );
    const res = await fetch("/v1/admin/settings/access", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ config, locked_keys: locked }),
    });
    if (res.ok) await load();
    return res.ok;
  };

  useSettingsFormHandle({
    id: "access-capabilities",
    title: "Capabilities",
    changes,
    save,
    discard: () => setGrid(initial),
  });

  const toggle = (capability: string, role: string, on: boolean) =>
    setGrid((prev) => {
      const held = new Set(prev[capability] ?? []);
      if (on) held.add(role);
      else held.delete(role);
      return { ...prev, [capability]: [...held] };
    });

  return (
    <section className="space-y-3" data-testid="capabilities-panel">
      <div>
        <h2 className="font-display text-base font-semibold text-foreground">Capabilities</h2>
        <p className="mt-1 text-xs text-neutral-600 dark:text-neutral-400">
          What each role may do beyond its own work. Owners hold every capability; viewers none.
        </p>
      </div>
      <div className="overflow-x-auto rounded-xl border border-surface-border bg-surface-card">
        <table className="w-full text-xs">
          <thead className="text-[10px] text-muted-foreground">
            <tr>
              <th scope="col" className="px-4 py-2 text-left">
                Capability
              </th>
              {ROLES.map((role) => (
                <th key={role} scope="col" className="px-4 py-2 text-center capitalize">
                  {role}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {CAPABILITIES.map((capability) => (
              <tr key={capability.key} className="border-t border-surface-border/60">
                <th scope="row" className="px-4 py-2 text-left font-normal">
                  <span className="block text-foreground">{capability.label}</span>
                  <span className="text-[10px] text-neutral-500">{capability.hint}</span>
                </th>
                {ROLES.map((role) => (
                  <td key={role} className="px-4 py-2 text-center">
                    <input
                      type="checkbox"
                      data-testid={`capability-${capability.key}-${role}`}
                      aria-label={`${capability.label}: ${role}`}
                      checked={(grid[capability.key] ?? []).includes(role)}
                      onChange={(e) => toggle(capability.key, role, e.target.checked)}
                      className="h-4 w-4 accent-orange-500"
                    />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
};
