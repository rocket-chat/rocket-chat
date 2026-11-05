"use client";

import React, { useCallback, useEffect, useState } from "react";
import { Plus, Workflow } from "lucide-react";
import { TemplatesSection } from "../../components/TemplatesSection";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { useConfirm } from "../../components/ui/ConfirmDialog";
import { EmptyState } from "../../components/ui/EmptyState";
import { PageBody, PageHeader } from "../../components/ui/PageHeader";
import { MissionCard } from "../../components/missions/MissionCard";
import { MissionForm } from "../../components/missions/MissionForm";
import { SecretDialog, type ShownSecrets } from "../../components/missions/SecretDialog";
import { OwnerFilterControl } from "../../components/settings/OwnerFilterControl";
import { useAccess } from "../../lib/access";
import { useTeams } from "../../lib/teams";
import { useOwnerFilter, useOwnerFilterSync } from "../../lib/useOwnerFilter";
import { creatableOwners, defaultOwner, groupByOwner, matchesOwner, ownerFields } from "../../lib/ownership";
import { blankMission, completeMission, type Mission, type MissionBody, type MissionPreset, type SavedMission } from "../../lib/missions";

type Editing = { kind: "new"; initial: MissionBody } | { kind: "edit"; mission: Mission } | null;

/**
 * Missions: agent runs the platform starts on its own (a schedule, a forge or tracker event,
 * its own webhook, or Run now), with where each result goes. Grouped by owner, like settings.
 */
export default function MissionsPage() {
  useOwnerFilterSync();
  const access = useAccess();
  const { teams } = useTeams();
  const [owner] = useOwnerFilter();
  const [confirm, confirmDialog] = useConfirm();
  const [missions, setMissions] = useState<Mission[]>([]);
  const [presets, setPresets] = useState<MissionPreset[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [editing, setEditing] = useState<Editing>(null);
  const [secrets, setSecrets] = useState<ShownSecrets | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [runsVersion, setRunsVersion] = useState(0);

  const load = useCallback(async () => {
    const res = await fetch("/v1/missions");
    if (res.ok) setMissions((await res.json()) as Mission[]);
    setLoaded(true);
  }, []);

  useEffect(() => {
    void load();
    fetch("/v1/missions/presets")
      .then((res) => (res.ok ? res.json() : []))
      .then((list: MissionPreset[]) => setPresets(Array.isArray(list) ? list : []))
      .catch(() => setPresets([]));
  }, [load]);

  const creatable = creatableOwners(access, teams, true);
  const startOwner = defaultOwner(owner, creatable);
  const groups = groupByOwner(
    missions.filter((m) => matchesOwner(m, owner)),
    teams,
  );

  const openNew = () => {
    if (!startOwner) return;
    setEditing({ kind: "new", initial: { ...blankMission(), ...ownerFields(startOwner) } });
  };

  const showSecrets = (saved: SavedMission) => {
    const origin = window.location.origin;
    if (!saved.inbound_secret && !saved.webhook_secret) return;
    setSecrets({
      missionName: saved.name,
      inbound: saved.inbound_secret && saved.inbound_path ? { url: `${origin}${saved.inbound_path}`, secret: saved.inbound_secret } : undefined,
      webhook: saved.webhook_secret ?? undefined,
    });
  };

  const onSaved = async (saved: SavedMission) => {
    setEditing(null);
    showSecrets(saved);
    await load();
  };

  const request = async (url: string, method: string, body?: unknown): Promise<Response | null> => {
    setError(null);
    const res = await fetch(url, {
      method,
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (!res.ok) {
      const detail = (await res.json().catch(() => ({}))).detail;
      setError(typeof detail === "string" ? detail : "That did not work.");
      return null;
    }
    return res;
  };

  const toggle = async (mission: Mission) => {
    // Shown at once; the reload below puts back what the API stored if it refused.
    setMissions((list) => list.map((m) => (m.id === mission.id ? { ...m, enabled: !m.enabled } : m)));
    await request(`/v1/missions/${mission.id}`, "PATCH", { enabled: !mission.enabled });
    await load();
  };

  const run = async (mission: Mission) => {
    const res = await request(`/v1/missions/${mission.id}/run`, "POST", {});
    if (res) {
      const outcome = (await res.json()) as { status: string; reason?: string };
      setNotice(outcome.reason ? `${mission.name}: ${outcome.reason}` : `${mission.name}: run ${outcome.status}.`);
    }
    setRunsVersion((v) => v + 1);
    await load();
  };

  const remove = async (mission: Mission) => {
    const ok = await confirm({ title: `Delete the mission "${mission.name}"?`, message: "Its runs stay as sessions.", confirmLabel: "Delete", tone: "danger" });
    if (!ok) return;
    await request(`/v1/missions/${mission.id}`, "DELETE");
    await load();
  };

  const rotate = async (mission: Mission) => {
    const ok = await confirm({
      title: `Make new secrets for "${mission.name}"?`,
      message: "The current ones stop working at once; callers need the new ones.",
      confirmLabel: "Rotate",
    });
    if (!ok) return;
    const shown: ShownSecrets = { missionName: mission.name };
    for (const which of ["inbound", "webhook"] as const) {
      if (which === "inbound" ? !mission.has_inbound_secret : !mission.has_webhook_secret) continue;
      const res = await request(`/v1/missions/${mission.id}/secrets/${which}`, "POST");
      if (!res) return;
      const { secret } = (await res.json()) as { secret: string };
      if (which === "inbound" && mission.inbound_path) shown.inbound = { url: `${window.location.origin}${mission.inbound_path}`, secret };
      if (which === "webhook") shown.webhook = secret;
    }
    setSecrets(shown);
  };

  return (
    <PageBody>
      {confirmDialog}
      {secrets && <SecretDialog secrets={secrets} onClose={() => setSecrets(null)} />}
      <PageHeader
        title="Missions"
        icon={<Workflow className="h-5 w-5" aria-hidden="true" />}
        description="Agent runs that start on their own: on a schedule, on a GitHub, GitLab, Jira or Linear event, through a webhook of their own, or when you press Run now. Each run is a session; the platform also posts its result where you say."
        actions={
          <Button data-testid="mission-new" variant="primary" disabled={!startOwner} onClick={openNew}>
            <Plus className="h-4 w-4" aria-hidden="true" />
            New mission
          </Button>
        }
      />

      <div className="mb-4 max-w-xs">
        <OwnerFilterControl />
      </div>

      {editing && (
        <Card className="mb-6 p-5">
          <MissionForm
            key={editing.kind === "edit" ? editing.mission.id : "new"}
            initial={editing.kind === "edit" ? completeMission(editing.mission) : editing.initial}
            editingId={editing.kind === "edit" ? editing.mission.id : undefined}
            presets={presets}
            onSaved={(saved) => void onSaved(saved)}
            onCancel={() => setEditing(null)}
          />
        </Card>
      )}

      {error && (
        <p role="alert" className="mb-4 text-sm text-rose-600 dark:text-rose-400">
          {error}
        </p>
      )}
      {notice && (
        <p role="status" data-testid="mission-notice" className="mb-4 text-sm text-neutral-700 dark:text-neutral-300">
          {notice}
        </p>
      )}

      {loaded && groups.length === 0 && !editing && (
        <EmptyState
          title="No mission yet"
          description="Start from a preset (a pull request review, a CI fixer, a weekly audit) or a blank mission."
          action={
            startOwner ? (
              <Button variant="primary" onClick={openNew}>
                New mission
              </Button>
            ) : undefined
          }
        />
      )}

      <div className="space-y-6">
        {groups.map((group) => (
          <section key={group.key} aria-labelledby={`missions-${group.key}`} data-testid={`mission-group-${group.key}`} className="space-y-2">
            <h2 id={`missions-${group.key}`} className="text-xs font-semibold text-neutral-600 dark:text-neutral-300">
              {group.label}
            </h2>
            <ul className="space-y-2">
              {group.items.map((mission) => (
                <li key={mission.id}>
                  <MissionCard
                    mission={mission}
                    runsVersion={runsVersion}
                    onToggle={(m) => void toggle(m)}
                    onRun={run}
                    onEdit={(m) => setEditing({ kind: "edit", mission: m })}
                    onDelete={(m) => void remove(m)}
                    onRotate={(m) => void rotate(m)}
                  />
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>

      <TemplatesSection />
    </PageBody>
  );
}
