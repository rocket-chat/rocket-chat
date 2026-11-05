"use client";

import React, { useEffect, useState } from "react";
import { FilePlus, SlidersHorizontal, Sparkles } from "lucide-react";
import { Button } from "../ui/Button";
import { Field, Input } from "../ui/Field";
import { OwnerSelect, useMoveConfirmation } from "../settings/Owner";
import { useAccess } from "../../lib/access";
import { useTeams } from "../../lib/teams";
import { creatableOwners, ownerFields, ownerKey, type OwnerKey } from "../../lib/ownership";
import {
  DELIVERY_OPTIONS,
  OWN_SOURCES,
  blankMission,
  completeMission,
  missionProblem,
  parseOutputSchema,
  sourceLabel,
  type MissionBody,
  type MissionDelivery,
  type MissionPreset,
  type SavedMission,
} from "../../lib/missions";
import { describeSchedule, readSchedule } from "../../lib/mission-schedule";
import { StepStrip, type MissionStep } from "./pipeline/StepStrip";
import { Tile, TileGroup } from "./pipeline/Tile";
import { WhenStep } from "./pipeline/WhenStep";
import { AgentStep, type Named } from "./pipeline/AgentStep";
import { TaskStep } from "./pipeline/TaskStep";
import { DeliverStep } from "./pipeline/DeliverStep";

/** The choices the form offers: agents, sandbox types and the organization's Slack bots. */
function useChoices(): { agents: Named[]; sandboxes: Named[]; bots: Named[] } {
  const [choices, setChoices] = useState<{ agents: Named[]; sandboxes: Named[]; bots: Named[] }>({ agents: [], sandboxes: [], bots: [] });
  useEffect(() => {
    let cancelled = false;
    const json = async <T,>(url: string, fallback: T): Promise<T> => {
      try {
        const res = await fetch(url);
        return res.ok ? ((await res.json()) as T) : fallback;
      } catch {
        return fallback;
      }
    };
    void (async () => {
      const [agents, sandboxes, bots] = await Promise.all([
        json<Named[]>("/v1/agents", []),
        json<{ sandboxes?: Named[] }>("/v1/sandboxes", {}),
        json<Named[]>("/v1/missions/chat-bots", []),
      ]);
      if (!cancelled) {
        setChoices({ agents: Array.isArray(agents) ? agents : [], sandboxes: sandboxes.sandboxes ?? [], bots: Array.isArray(bots) ? bots : [] });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);
  return choices;
}

const schemaTexts = (deliveries: MissionDelivery[]): Record<number, string> =>
  Object.fromEntries(
    deliveries.map((d, i) => [i, d.kind === "webhook" && Object.keys(d.output_schema).length ? JSON.stringify(d.output_schema, null, 2) : ""]),
  );

/** Whether the mission uses a setting only Advanced shows: it then opens there, hiding nothing. */
function usesAdvanced(draft: MissionBody, schemas: Record<number, string>): boolean {
  const triggerNeedsIt = draft.triggers.some(
    (t) =>
      (t.source === "schedule" && readSchedule(t.schedule) === null) ||
      (!OWN_SOURCES.has(t.source) && t.filter.repositories.length + t.filter.labels.length + t.filter.branches.length > 0),
  );
  return (
    triggerNeedsIt ||
    !!(draft.permission_mode || draft.model_tier || draft.sandbox_type || draft.git_branch) ||
    Object.values(schemas).some((text) => text.trim() !== "")
  );
}

function summaries(draft: MissionBody, agents: Named[]): Record<MissionStep, string> {
  const when = draft.triggers.length
    ? draft.triggers.map((t) => (t.source === "schedule" ? describeSchedule(t.schedule, t.timezone) : sourceLabel(t.source))).join(" · ")
    : "Run by hand";
  const agentName = agents.find((a) => a.id === draft.agent_id)?.name ?? (draft.mode === "review" ? "The reviewer" : "The default agent");
  const instructions = draft.instructions.trim().replace(/\s+/g, " ");
  const task = instructions ? (instructions.length > 70 ? `${instructions.slice(0, 69)}…` : instructions) : "No instructions yet";
  const deliveries = draft.deliveries.map((d) => DELIVERY_OPTIONS.find((o) => o.kind === d.kind)?.label ?? d.kind);
  return {
    when,
    agent: `${agentName} · ${draft.mode === "review" ? "reviews the pull request" : "does the work"}`,
    task: draft.git_repo ? `${task} (${draft.git_repo})` : task,
    deliver: ["Its session", ...deliveries].join(" + "),
  };
}

interface MissionFormProps {
  /** The mission edited (a stored one) or the starting point of a new one. */
  initial: MissionBody;
  editingId?: string;
  presets: MissionPreset[];
  onSaved: (saved: SavedMission) => void;
  onCancel: () => void;
}

/** One mission as a flow: when it runs, which agent, what it does, and where its result goes. */
export const MissionForm: React.FC<MissionFormProps> = ({ initial, editingId, presets, onSaved, onCancel }) => {
  const access = useAccess();
  const { teams } = useTeams();
  const choices = useChoices();
  const [confirmMove, moveDialog] = useMoveConfirmation();
  const owners = creatableOwners(access, teams, true);
  const storedOwner = editingId ? ownerKey(initial) : null;
  const [draft, setDraft] = useState<MissionBody>(initial);
  const [owner, setOwner] = useState<OwnerKey>(ownerKey(initial));
  const [schemas, setSchemas] = useState<Record<number, string>>(() => schemaTexts(initial.deliveries));
  const [advanced, setAdvanced] = useState(() => usesAdvanced(initial, schemaTexts(initial.deliveries)));
  const [step, setStep] = useState<MissionStep>("when");
  const [presetId, setPresetId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const patch = (changes: Partial<MissionBody>) => setDraft((current) => ({ ...current, ...changes }));

  const applyPreset = (id: string) => {
    const preset = presets.find((p) => p.id === id);
    setPresetId(id);
    setDraft({ ...(preset ? completeMission(preset.mission) : blankMission()), name: draft.name, enabled: true });
    setSchemas({});
  };

  const ownerTeam = owner.startsWith("team:") ? owner.slice(5) : null;
  const sandboxes = choices.sandboxes.filter((box) => !box.team_id || box.team_id === ownerTeam);

  const save = async (event: React.FormEvent) => {
    event.preventDefault();
    setError(null);
    const deliveries: MissionDelivery[] = [];
    for (const [index, delivery] of draft.deliveries.entries()) {
      if (delivery.kind !== "webhook") {
        deliveries.push(delivery);
        continue;
      }
      const parsed = parseOutputSchema(schemas[index] ?? "");
      if ("error" in parsed) return setError(parsed.error);
      deliveries.push({ ...delivery, output_schema: parsed.schema });
    }
    const body: MissionBody = { ...draft, deliveries, ...ownerFields(owner) };
    const problem = missionProblem(body);
    if (problem) return setError(problem);
    if (editingId && storedOwner && !(await confirmMove(body.name, storedOwner, owner, { kind: "mission", id: editingId }))) return;
    setBusy(true);
    try {
      const res = await fetch(editingId ? `/v1/missions/${encodeURIComponent(editingId)}` : "/v1/missions", {
        method: editingId ? "PUT" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const saved = (await res.json().catch(() => ({}))) as SavedMission & { detail?: unknown };
      if (!res.ok) return setError(typeof saved.detail === "string" ? saved.detail : "The mission was not saved.");
      onSaved(saved);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={(e) => void save(e)} data-testid="mission-form" className="space-y-6">
      {moveDialog}
      <div className="flex flex-wrap items-end gap-4">
        <Field label="Name" className="min-w-[14rem] flex-1">
          {(control) => <Input {...control} data-testid="mission-name" required value={draft.name} onChange={(e) => patch({ name: e.target.value })} />}
        </Field>
        <OwnerSelect testId="mission-owner" value={owner} options={owners} onChange={setOwner} />
        <div role="group" aria-label="How much of the mission to show" className="mb-0.5 inline-flex rounded-lg border border-surface-border p-0.5">
          {([
            ["simple", "Simple", false],
            ["advanced", "Advanced", true],
          ] as const).map(([id, label, value]) => (
            <button
              key={id}
              type="button"
              data-testid={`mission-${id}`}
              aria-pressed={advanced === value}
              onClick={() => setAdvanced(value)}
              className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs ${
                advanced === value ? "bg-brand/15 font-semibold text-foreground" : "text-neutral-600 hover:text-foreground dark:text-neutral-300"
              }`}
            >
              {value && <SlidersHorizontal className="h-3.5 w-3.5" aria-hidden="true" />}
              {label}
            </button>
          ))}
        </div>
      </div>

      {!editingId && presets.length > 0 && (
        <section aria-labelledby="mission-presets-title" className="space-y-2">
          <h3 id="mission-presets-title" className="text-xs font-semibold text-foreground">
            Start from
          </h3>
          <TileGroup label="Start from a preset">
            <Tile icon={FilePlus} label="Blank mission" selected={presetId === ""} testId="mission-preset-blank" onClick={() => applyPreset("")} />
            {presets.map((preset) => (
              <Tile
                key={preset.id}
                icon={Sparkles}
                label={preset.name}
                hint={preset.description}
                selected={presetId === preset.id}
                testId={`mission-preset-${preset.id}`}
                onClick={() => applyPreset(preset.id)}
              />
            ))}
          </TileGroup>
        </section>
      )}

      <StepStrip active={step} summaries={summaries(draft, choices.agents)} onChoose={setStep} />

      <section data-testid={`mission-panel-${step}`} aria-label="Selected step" className="rounded-lg border border-surface-border p-4">
        {step === "when" && <WhenStep triggers={draft.triggers} advanced={advanced} onChange={(triggers) => patch({ triggers })} />}
        {step === "agent" && <AgentStep draft={draft} agents={choices.agents} sandboxes={sandboxes} advanced={advanced} patch={patch} />}
        {step === "task" && <TaskStep draft={draft} advanced={advanced} patch={patch} />}
        {step === "deliver" && (
          <DeliverStep
            deliveries={draft.deliveries}
            schemas={schemas}
            bots={choices.bots}
            advanced={advanced}
            onChange={(deliveries) => patch({ deliveries })}
            onSchemas={setSchemas}
          />
        )}
      </section>

      {error && (
        <p role="alert" data-testid="mission-error" className="text-sm text-rose-600 dark:text-rose-400">
          {error}
        </p>
      )}
      <div className="flex gap-2">
        <Button type="submit" variant="primary" data-testid="mission-save" disabled={busy}>
          {editingId ? "Save mission" : "Create mission"}
        </Button>
        <Button variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  );
};
