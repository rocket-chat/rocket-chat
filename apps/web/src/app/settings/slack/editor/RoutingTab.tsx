"use client";

import React, { useState } from "react";
import { ChevronRight, Hash, MessageSquareText, Plus, Star, Trash2, type LucideIcon } from "lucide-react";
import { Input, Select } from "../../../../components/ui/Field";
import { ClassifierSettings } from "./ClassifierSettings";
import { RouterTryBox } from "./RouterTryBox";
import { RoutingRowDetails, useRoutingCatalogue } from "./RoutingRowDetails";
import { blankRow, type RowKind, type RoutingRow } from "./routing";
import type { BotEditorState } from "./useBotEditor";

type AgentOption = { value: string; label: string };

const KIND_LABEL: Record<RowKind, string> = { default: "Default", channel: "Channel", intent: "Intent" };
const KIND_ICON: Record<RowKind, LucideIcon> = {
  default: Star,
  channel: Hash,
  intent: MessageSquareText,
};
const CELL = "h-8 text-xs";

function emptyAgentLabel(kind: RowKind): string | null {
  if (kind === "channel") return "(Default row's agent)";
  if (kind === "intent") return "Choose an agent";
  return null;
}

interface RowProps {
  row: RoutingRow;
  index: number;
  agentOptions: AgentOption[];
  expanded: boolean;
  onToggle: () => void;
  onChange: (changes: Partial<RoutingRow>) => void;
  onRemove: () => void;
}

const MatchCell: React.FC<{ row: RoutingRow; index: number; onChange: RowProps["onChange"] }> = ({ row, index, onChange }) => {
  if (row.kind === "default") return <span className="text-xs text-muted-foreground">Everything else</span>;
  return (
    <Input
      aria-label={row.kind === "channel" ? `Channel id of row ${index + 1}` : `Intent name of row ${index + 1}`}
      data-testid={`routing-match-${index}`}
      value={row.match}
      onChange={(e) => onChange({ match: e.target.value })}
      placeholder={row.kind === "channel" ? "C0123ABCD" : "bug_report"}
      className={CELL}
    />
  );
};

const RoutingTableRow: React.FC<RowProps> = ({ row, index, agentOptions, expanded, onToggle, onChange, onRemove }) => {
  const Icon = KIND_ICON[row.kind];
  const empty = emptyAgentLabel(row.kind);
  const name = row.match.trim() || `row ${index + 1}`;
  return (
    <tr data-testid={`routing-row-${index}`} className="align-middle">
      <td className="p-2">
        <span className="inline-flex items-center gap-1 rounded border border-border px-1.5 py-0.5 text-[10px] text-foreground">
          <Icon className="h-3 w-3" aria-hidden="true" /> {KIND_LABEL[row.kind]}
        </span>
      </td>
      <td className="p-2">
        <MatchCell row={row} index={index} onChange={onChange} />
      </td>
      <td className="p-2">
        <Select aria-label={`Agent of ${name}`} data-testid={`routing-agent-${index}`} value={row.agent_id} onChange={(e) => onChange({ agent_id: e.target.value })} className={CELL}>
          {empty && <option value="">{empty}</option>}
          {agentOptions.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </Select>
      </td>
      <td className="p-2">
        {row.kind === "default" ? (
          <span className="text-[11px] text-muted-foreground">Chosen in the conversation</span>
        ) : (
          <Input aria-label={`Repository of ${name}`} data-testid={`routing-repository-${index}`} value={row.repository} onChange={(e) => onChange({ repository: e.target.value })} placeholder="owner/name" className={CELL} />
        )}
      </td>
      <td className="p-2">
        {row.kind !== "default" && (
          <Input aria-label={`Branch of ${name}`} data-testid={`routing-branch-${index}`} value={row.branch} onChange={(e) => onChange({ branch: e.target.value })} placeholder="main" className={CELL} />
        )}
      </td>
      <td className="whitespace-nowrap p-2 text-right">
        {row.kind !== "default" && (
          <>
            <button type="button" aria-expanded={expanded} aria-label={`More settings of ${name}`} data-testid={`routing-details-${index}`} onClick={onToggle} className="rounded p-1.5 text-muted-foreground hover:text-foreground">
              <ChevronRight className={`h-3.5 w-3.5 transition-transform ${expanded ? "rotate-90" : ""}`} aria-hidden="true" />
            </button>
            <button type="button" aria-label={`Remove ${name}`} data-testid={`routing-remove-${index}`} onClick={onRemove} className="rounded p-1.5 text-muted-foreground hover:text-rose-500">
              <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
            </button>
          </>
        )}
      </td>
    </tr>
  );
};

/** One table maps a channel or an intent to an agent, a repository and a branch; the default row
 * answers everything else. Intents are recognised by a classifier, set up below the table. */
export const RoutingTab: React.FC<{ editor: BotEditorState; agentOptions: AgentOption[] }> = ({ editor, agentOptions }) => {
  const { bot, form, patch } = editor;
  const [expanded, setExpanded] = useState<number | null>(null);
  const catalogue = useRoutingCatalogue();
  const rows = form.rows;
  const hasIntents = rows.some((row) => row.kind === "intent");

  const update = (key: number, changes: Partial<RoutingRow>) =>
    patch({ rows: rows.map((row) => (row.key === key ? { ...row, ...changes } : row)) });
  const add = (kind: RowKind) => {
    const key = Math.max(0, ...rows.map((row) => row.key)) + 1;
    patch({ rows: [...rows, blankRow(kind, key, kind === "intent" ? (agentOptions[0]?.value ?? "") : "")] });
  };

  return (
    <div className="space-y-5">
      <p className="text-xs text-muted-foreground">
        Who answers a message: its channel&apos;s row first, then the intent the classifier recognises, then the default row.
        The sessions a row starts are told its repository and branch and clone them first.
      </p>
      <div className="overflow-x-auto rounded-lg border border-border">
        <table className="w-full text-left text-xs" data-testid="routing-table">
          <thead className="border-b border-border bg-surface-subnav/80 text-muted-foreground">
            <tr>
              <th className="p-2 font-medium">Kind</th>
              <th className="p-2 font-medium">Channel or intent</th>
              <th className="p-2 font-medium">Agent</th>
              <th className="p-2 font-medium">Repository</th>
              <th className="p-2 font-medium">Branch</th>
              <th className="p-2">
                <span className="sr-only">Actions</span>
              </th>
            </tr>
          </thead>
          {rows.map((row, index) => (
            <tbody key={row.key} className="border-b border-border/60 last:border-b-0">
              <RoutingTableRow
                row={row}
                index={index}
                agentOptions={agentOptions}
                expanded={expanded === row.key}
                onToggle={() => setExpanded(expanded === row.key ? null : row.key)}
                onChange={(changes) => update(row.key, changes)}
                onRemove={() => patch({ rows: rows.filter((r) => r.key !== row.key) })}
              />
              {expanded === row.key && (
                <tr>
                  <td colSpan={6} className="bg-surface-subnav/30 p-3" data-testid={`routing-row-details-${index}`}>
                    <RoutingRowDetails row={row} index={index} catalogue={catalogue} onChange={(changes) => update(row.key, changes)} />
                  </td>
                </tr>
              )}
            </tbody>
          ))}
        </table>
      </div>
      <div className="flex flex-wrap gap-2">
        <button type="button" data-testid="routing-add-channel" onClick={() => add("channel")} className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs text-foreground hover:bg-surface-elevated">
          <Plus className="h-3.5 w-3.5" aria-hidden="true" /> Add a channel
        </button>
        <button type="button" data-testid="routing-add-intent" onClick={() => add("intent")} className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs text-foreground hover:bg-surface-elevated">
          <Plus className="h-3.5 w-3.5" aria-hidden="true" /> Add an intent
        </button>
      </div>
      {hasIntents && (
        <ClassifierSettings value={form.classifier} rows={rows} agentOptions={agentOptions} onChange={(classifier) => patch({ classifier })} />
      )}
      {bot?.router && hasIntents && <RouterTryBox botId={bot.id} />}
    </div>
  );
};
