"use client";

import React from "react";
import {
  TriangleAlert,
  Clock,
  GitMerge,
  GitPullRequest,
  MessageSquare,
  Plus,
  Tag,
  Ticket,
  Trash2,
  Webhook,
  Zap,
  type LucideIcon,
} from "lucide-react";
import { Button } from "../../ui/Button";
import { Field, Input, Select } from "../../ui/Field";
import { MAX_TRIGGERS, OWN_SOURCES, TRIGGER_SOURCES, blankTrigger, type MissionTrigger } from "../../../lib/missions";
import { COMMON_TIMEZONES, FREQUENCIES, readSchedule, writeSchedule, type Frequency } from "../../../lib/mission-schedule";
import { Tile, TileGroup } from "./Tile";

const SOURCE_ICONS: Record<string, LucideIcon> = {
  schedule: Clock,
  webhook: Webhook,
  github_pr: GitPullRequest,
  github_issue_label: Tag,
  github_issue_comment: MessageSquare,
  github_pr_review_comment: MessageSquare,
  github_workflow_failed: TriangleAlert,
  gitlab_mr: GitMerge,
  gitlab_issue_label: Tag,
  gitlab_note: MessageSquare,
  gitlab_pipeline_failed: TriangleAlert,
  jira_issue: Ticket,
  linear_issue: Ticket,
};

// The sources most missions start from; Advanced lists every one.
const SIMPLE_SOURCES = new Set(["schedule", "webhook", "github_pr", "github_issue_label", "github_workflow_failed"]);

const tileLabel = (label: string, group: string) => (group === "Its own" ? label : `${group}: ${label}`);

const list = (value: string) =>
  value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);

const SchedulePicker: React.FC<{ index: number; trigger: MissionTrigger; advanced: boolean; onChange: (t: MissionTrigger) => void }> = ({
  index,
  trigger,
  advanced,
  onChange,
}) => {
  const friendly = readSchedule(trigger.schedule);
  const zones = COMMON_TIMEZONES.includes(trigger.timezone) ? COMMON_TIMEZONES : [trigger.timezone, ...COMMON_TIMEZONES];
  const setFriendly = (frequency: Frequency, time: string) => onChange({ ...trigger, schedule: writeSchedule({ frequency, time }) });
  return (
    <div className="space-y-3">
      {friendly ? (
        <div className="grid gap-3 sm:grid-cols-3">
          <Field label="Runs">
            {(control) => (
              <Select
                {...control}
                data-testid={`mission-trigger-frequency-${index}`}
                value={friendly.frequency}
                onChange={(e) => setFriendly(e.target.value as Frequency, friendly.time)}
              >
                {FREQUENCIES.map((f) => (
                  <option key={f.value} value={f.value}>
                    {f.label}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          {friendly.frequency !== "hourly" && (
            <Field label="At">
              {(control) => (
                <Input
                  {...control}
                  type="time"
                  data-testid={`mission-trigger-time-${index}`}
                  value={friendly.time}
                  onChange={(e) => setFriendly(friendly.frequency, e.target.value)}
                />
              )}
            </Field>
          )}
          {!advanced && (
            <Field label="Time zone">
              {(control) => (
                <Select {...control} value={trigger.timezone} onChange={(e) => onChange({ ...trigger, timezone: e.target.value })}>
                  {zones.map((zone) => (
                    <option key={zone} value={zone}>
                      {zone}
                    </option>
                  ))}
                </Select>
              )}
            </Field>
          )}
        </div>
      ) : (
        !advanced && (
          <p data-testid={`mission-trigger-custom-${index}`} className="text-xs text-neutral-600 dark:text-neutral-400">
            A custom schedule (<code className="font-mono">{trigger.schedule}</code>, {trigger.timezone}): change it in Advanced.
          </p>
        )
      )}
      {advanced && (
        <div className="grid gap-3 sm:grid-cols-3">
          <Field label="Schedule (cron)" hint="Five fields, at most every 5 minutes" className="sm:col-span-2">
            {(control) => (
              <Input
                {...control}
                data-testid={`mission-trigger-cron-${index}`}
                className="font-mono"
                value={trigger.schedule}
                onChange={(e) => onChange({ ...trigger, schedule: e.target.value })}
              />
            )}
          </Field>
          <Field label="Time zone" hint="An IANA name, e.g. Europe/Paris">
            {(control) => <Input {...control} value={trigger.timezone} onChange={(e) => onChange({ ...trigger, timezone: e.target.value })} />}
          </Field>
        </div>
      )}
    </div>
  );
};

const EventFilters: React.FC<{ trigger: MissionTrigger; onChange: (t: MissionTrigger) => void }> = ({ trigger, onChange }) => {
  const setFilter = (key: keyof MissionTrigger["filter"], value: string) =>
    onChange({ ...trigger, filter: { ...trigger.filter, [key]: list(value) } });
  return (
    <div className="grid gap-3 sm:grid-cols-3">
      <Field label="Repositories" hint="owner/name, globs allowed; empty: any">
        {(control) => (
          <Input {...control} placeholder="acme/*" value={trigger.filter.repositories.join(", ")} onChange={(e) => setFilter("repositories", e.target.value)} />
        )}
      </Field>
      <Field label="Labels" hint="Any of these; empty: any">
        {(control) => <Input {...control} value={trigger.filter.labels.join(", ")} onChange={(e) => setFilter("labels", e.target.value)} />}
      </Field>
      <Field label="Branches" hint="Globs; empty: any">
        {(control) => <Input {...control} value={trigger.filter.branches.join(", ")} onChange={(e) => setFilter("branches", e.target.value)} />}
      </Field>
    </div>
  );
};

const TriggerEditor: React.FC<{
  index: number;
  trigger: MissionTrigger;
  advanced: boolean;
  onChange: (trigger: MissionTrigger) => void;
  onRemove: () => void;
}> = ({ index, trigger, advanced, onChange, onRemove }) => {
  const sources = TRIGGER_SOURCES.filter((s) => advanced || SIMPLE_SOURCES.has(s.value) || s.value === trigger.source);
  const filtered = trigger.filter.repositories.length + trigger.filter.labels.length + trigger.filter.branches.length > 0;
  return (
    <li className="space-y-3 rounded-lg border border-surface-border p-3" data-testid={`mission-trigger-${index}`}>
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-semibold text-foreground">Trigger {index + 1}</span>
        <Button size="sm" variant="ghost" aria-label={`Remove trigger ${index + 1}`} onClick={onRemove}>
          <Trash2 className="h-4 w-4" aria-hidden="true" />
        </Button>
      </div>
      <TileGroup label={`What starts trigger ${index + 1}`}>
        {sources.map((source) => (
          <Tile
            key={source.value}
            icon={SOURCE_ICONS[source.value] ?? Zap}
            label={tileLabel(source.label, source.group)}
            selected={trigger.source === source.value}
            testId={`mission-trigger-${index}-source-${source.value}`}
            onClick={() => onChange({ ...blankTrigger(source.value), filter: trigger.filter })}
          />
        ))}
      </TileGroup>
      {trigger.source === "schedule" && <SchedulePicker index={index} trigger={trigger} advanced={advanced} onChange={onChange} />}
      {trigger.source === "webhook" && (
        <p className="text-xs text-neutral-600 dark:text-neutral-400">
          The mission gets a URL of its own and a secret to sign calls with; both are shown once it is saved.
        </p>
      )}
      {!OWN_SOURCES.has(trigger.source) &&
        (advanced ? (
          <EventFilters trigger={trigger} onChange={onChange} />
        ) : (
          <p className="text-xs text-neutral-600 dark:text-neutral-400">
            {filtered ? "Only the repositories, labels or branches set in Advanced." : "Any repository the mission can reach; narrow it in Advanced."}
          </p>
        ))}
    </li>
  );
};

/** When the mission runs: up to three triggers, or none (it then runs when someone presses Run now). */
export const WhenStep: React.FC<{
  triggers: MissionTrigger[];
  advanced: boolean;
  onChange: (triggers: MissionTrigger[]) => void;
}> = ({ triggers, advanced, onChange }) => (
  <div className="space-y-3">
    {triggers.length === 0 && (
      <p className="text-xs text-neutral-600 dark:text-neutral-400">No trigger: it runs when someone presses Run now.</p>
    )}
    <ul className="space-y-3">
      {triggers.map((trigger, index) => (
        <TriggerEditor
          key={index}
          index={index}
          trigger={trigger}
          advanced={advanced}
          onChange={(next) => onChange(triggers.map((t, i) => (i === index ? next : t)))}
          onRemove={() => onChange(triggers.filter((_, i) => i !== index))}
        />
      ))}
    </ul>
    <Button
      size="sm"
      data-testid="mission-trigger-add"
      disabled={triggers.length >= MAX_TRIGGERS}
      onClick={() => onChange([...triggers, blankTrigger(triggers.length === 0 ? "schedule" : "github_pr")])}
    >
      <Plus className="h-3.5 w-3.5" aria-hidden="true" /> Add a trigger
    </Button>
  </div>
);
