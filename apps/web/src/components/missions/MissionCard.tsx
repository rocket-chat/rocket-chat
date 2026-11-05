"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { History, KeyRound, Pencil, Play, Trash2 } from "lucide-react";
import { Badge, type BadgeTone } from "../ui/Badge";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { OwnerBadge } from "../settings/Owner";
import { deliveryLabel, scheduleLabel, sourceLabel, type Mission, type MissionRun } from "../../lib/missions";

const formatTime = (epoch: number | null) => (epoch ? new Date(epoch * 1000).toLocaleString() : "—");

const STATUS_TONE: Record<string, BadgeTone> = {
  running: "neutral",
  completed: "success",
  delivered: "success",
  failed: "danger",
  delivery_failed: "warning",
};

function triggerSummary(mission: Mission): string {
  if (mission.triggers.length === 0) return "Run by hand";
  return mission.triggers
    .map((t) => (t.source === "schedule" ? scheduleLabel(t.schedule) : sourceLabel(t.source)))
    .join(" · ");
}

/** The latest runs of a mission: how each went, how each delivery went, and its session. */
const RunsList: React.FC<{ missionId: string; refresh: number }> = ({ missionId, refresh }) => {
  const [runs, setRuns] = useState<MissionRun[] | null>(null);
  const load = useCallback(async () => {
    const res = await fetch(`/v1/missions/${encodeURIComponent(missionId)}/runs`);
    setRuns(res.ok ? ((await res.json()) as MissionRun[]) : []);
  }, [missionId]);
  useEffect(() => {
    void load();
  }, [load, refresh]);
  // A run still going changes on its own: look again while one does.
  useEffect(() => {
    if (!runs?.some((r) => r.status === "running")) return;
    const timer = setTimeout(() => void load(), 3000);
    return () => clearTimeout(timer);
  }, [runs, load]);
  if (runs === null) return <p className="text-xs text-neutral-600 dark:text-neutral-400">Loading runs…</p>;
  if (runs.length === 0) return <p className="text-xs text-neutral-600 dark:text-neutral-400">No run yet.</p>;
  return (
    <ul className="divide-y divide-border/60 rounded-lg border border-border" data-testid="mission-runs" aria-label="Runs">
      {runs.map((run) => (
        <li key={run.id} className="flex flex-wrap items-center gap-2 px-3 py-2 text-xs" data-testid="mission-run">
          <Badge tone={STATUS_TONE[run.status] ?? "neutral"}>{run.status.replace("_", " ")}</Badge>
          <span className="text-neutral-700 dark:text-neutral-300">{formatTime(run.started_at)}</span>
          <span className="text-neutral-600 dark:text-neutral-400">{sourceLabel(run.source)}</span>
          {run.deliveries.map((d) => (
            <Badge key={d.kind} tone={d.status === "delivered" ? "success" : d.status === "failed" ? "danger" : "neutral"} title={d.error || undefined}>
              {deliveryLabel(d.kind)}: {d.status}
            </Badge>
          ))}
          {run.session_id && (
            <Link href={`/chat/${run.session_id}`} className="ml-auto text-brand underline-offset-2 hover:underline">
              Open the session
            </Link>
          )}
        </li>
      ))}
    </ul>
  );
};

interface MissionCardProps {
  mission: Mission;
  onToggle: (mission: Mission) => void;
  onRun: (mission: Mission) => Promise<void>;
  onEdit: (mission: Mission) => void;
  onDelete: (mission: Mission) => void;
  onRotate: (mission: Mission) => void;
  /** Bumped after a run starts, so an open run list reads again. */
  runsVersion: number;
}

/** One mission: when it runs and where its result goes, on/off, Run now and its runs. */
export const MissionCard: React.FC<MissionCardProps> = ({ mission, onToggle, onRun, onEdit, onDelete, onRotate, runsVersion }) => {
  const [showRuns, setShowRuns] = useState(false);
  const [running, setRunning] = useState(false);
  const deliveries = ["session", ...mission.deliveries.map((d) => d.kind).filter((k) => k !== "session")];
  const run = async () => {
    setRunning(true);
    try {
      await onRun(mission);
      setShowRuns(true);
    } finally {
      setRunning(false);
    }
  };
  return (
    <Card className="space-y-3 p-4" data-testid="mission-item">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="truncate text-sm font-semibold text-foreground">{mission.name}</h3>
            <OwnerBadge owned={mission} />
            {mission.mode === "review" && <Badge>review</Badge>}
            {!mission.enabled && <Badge tone="warning">paused</Badge>}
          </div>
          <p className="text-xs text-neutral-700 dark:text-neutral-300">
            <span className="font-medium">When:</span> {triggerSummary(mission)} · <span className="font-medium">Deliver to:</span>{" "}
            {deliveries.map(deliveryLabel).join(", ")}
          </p>
          <p className="text-xs text-neutral-600 dark:text-neutral-400">
            Next run {formatTime(mission.next_run_at)} · Last run {formatTime(mission.last_run_at)}
            {mission.last_status ? ` (${mission.last_status})` : ""}
            {mission.last_session_id && (
              <>
                {" · "}
                <Link href={`/chat/${mission.last_session_id}`} className="text-brand underline-offset-2 hover:underline">
                  open last run
                </Link>
              </>
            )}
          </p>
          {mission.inbound_path && (
            <p className="truncate font-mono text-[11px] text-neutral-600 dark:text-neutral-400" data-testid="mission-inbound-path">
              Webhook: POST {mission.inbound_path}
            </p>
          )}
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-1">
          {mission.can_manage && (
            <label className="mr-1 inline-flex items-center gap-1.5 text-xs text-foreground">
              <input type="checkbox" data-testid="mission-toggle" checked={mission.enabled} onChange={() => onToggle(mission)} />
              On
            </label>
          )}
          {mission.can_manage && (
            <Button size="sm" variant="secondary" data-testid="mission-run" disabled={running} onClick={() => void run()}>
              <Play className="h-3.5 w-3.5" aria-hidden="true" /> Run now
            </Button>
          )}
          <Button size="sm" variant="ghost" data-testid="mission-runs-toggle" aria-expanded={showRuns} onClick={() => setShowRuns((open) => !open)}>
            <History className="h-3.5 w-3.5" aria-hidden="true" /> Runs
          </Button>
          {mission.can_manage && (mission.has_inbound_secret || mission.has_webhook_secret) && (
            <Button size="sm" variant="ghost" aria-label={`Rotate the secrets of ${mission.name}`} title="Rotate secrets" onClick={() => onRotate(mission)}>
              <KeyRound className="h-3.5 w-3.5" aria-hidden="true" />
            </Button>
          )}
          {mission.can_manage && (
            <Button size="sm" variant="ghost" data-testid="mission-edit" aria-label={`Edit ${mission.name}`} onClick={() => onEdit(mission)}>
              <Pencil className="h-3.5 w-3.5" aria-hidden="true" />
            </Button>
          )}
          {mission.can_manage && (
            <Button size="sm" variant="ghost" aria-label={`Delete ${mission.name}`} onClick={() => onDelete(mission)}>
              <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
            </Button>
          )}
        </div>
      </div>
      {showRuns && <RunsList missionId={mission.id} refresh={runsVersion} />}
    </Card>
  );
};
