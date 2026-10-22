"use client";

import React, { useEffect, useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { Field, Input, Select, Textarea } from "../../../components/ui/Field";
import type { ChannelProfile } from "./types";

interface ChannelProfilesEditorProps {
  value: Record<string, ChannelProfile>;
  agentOptions: { value: string; label: string }[];
  onChange: (next: Record<string, ChannelProfile>) => void;
}

interface Row {
  key: number;
  channel: string;
  profile: ChannelProfile;
}

const INSTRUCTIONS_LIMIT = 2000;

function toRows(value: Record<string, ChannelProfile>): Row[] {
  return Object.entries(value).map(([channel, profile], key) => ({ key, channel, profile }));
}

function toRecord(rows: Row[]): Record<string, ChannelProfile> {
  const record: Record<string, ChannelProfile> = {};
  for (const { channel, profile } of rows) {
    const id = channel.trim();
    if (id) record[id] = profile;
  }
  return record;
}

function splitTools(text: string): string[] {
  return text
    .split(",")
    .map((tool) => tool.trim())
    .filter(Boolean);
}

/** One row per Slack channel: its persona, house rules, tool scope, sandbox image and response mode. */
export function ChannelProfilesEditor({ value, agentOptions, onChange }: ChannelProfilesEditorProps) {
  const [rows, setRows] = useState<Row[]>(() => toRows(value));
  const [toolDrafts, setToolDrafts] = useState<Record<number, string>>({});
  const [images, setImages] = useState<{ value: string; label: string }[]>([]);
  const [toolNames, setToolNames] = useState<string[]>([]);

  useEffect(() => {
    fetch("/v1/sandboxes")
      .then((res) => (res.ok ? res.json() : { sandboxes: [] }))
      .then((data: { sandboxes: { name: string; image: string }[] }) =>
        setImages(data.sandboxes.map((s) => ({ value: s.image, label: `${s.name} (${s.image})` }))),
      )
      .catch(() => {});
    fetch("/v1/tools")
      .then((res) => (res.ok ? res.json() : { tools: [] }))
      .then((data: { tools: { name: string }[] }) => setToolNames(data.tools.map((t) => t.name)))
      .catch(() => {});
  }, []);

  const commit = (next: Row[]) => {
    setRows(next);
    onChange(toRecord(next));
  };

  const patchRow = (key: number, patch: Partial<Row>) =>
    commit(rows.map((row) => (row.key === key ? { ...row, ...patch } : row)));

  const patchProfile = (row: Row, patch: Partial<ChannelProfile>) =>
    patchRow(row.key, { profile: { ...row.profile, ...patch } });

  const addRow = () => commit([...rows, { key: Math.max(-1, ...rows.map((r) => r.key)) + 1, channel: "", profile: {} }]);

  const duplicates = new Set(
    rows.map((r) => r.channel.trim()).filter((id, index, all) => id && all.indexOf(id) !== index),
  );

  return (
    <div className="space-y-4" data-testid="channel-profiles-editor">
      <p className="text-xs text-muted-foreground">
        Give a channel its own persona, house rules, tool scope and sandbox image. Channels without a profile, and direct
        messages, use the bot&apos;s defaults. A tool list can only narrow what the organization allows.
      </p>

      {rows.length === 0 && (
        <p className="p-4 rounded-lg border border-dashed border-border text-center text-xs text-muted-foreground">
          No channel profiles yet.
        </p>
      )}

      {rows.map((row, index) => {
        const channelId = row.channel.trim();
        const tools = toolDrafts[row.key] ?? (row.profile.allowed_tools ?? []).join(", ");
        const imageKnown = !row.profile.sandbox_image || images.some((i) => i.value === row.profile.sandbox_image);
        return (
          <fieldset
            key={row.key}
            className="space-y-3 p-4 rounded-lg border border-border bg-surface-card"
            data-testid={`channel-profile-${index}`}
          >
            <legend className="sr-only">Channel profile {index + 1}</legend>
            <div className="flex items-end gap-3">
              <Field
                label="Channel ID"
                hint="The Slack channel id, e.g. C0123ABCD (channel details → bottom of the About tab)."
                error={duplicates.has(channelId) ? "This channel already has a profile." : null}
                className="flex-1"
              >
                {(props) => (
                  <Input
                    {...props}
                    value={row.channel}
                    onChange={(e) => patchRow(row.key, { channel: e.target.value })}
                    placeholder="C0123ABCD"
                    data-testid={`channel-profile-id-${index}`}
                  />
                )}
              </Field>
              <button
                type="button"
                onClick={() => commit(rows.filter((r) => r.key !== row.key))}
                aria-label={`Remove profile ${channelId || index + 1}`}
                className="mb-6 p-2 rounded-lg border border-border text-muted-foreground hover:text-rose-500 hover:bg-surface-elevated"
              >
                <Trash2 className="w-3.5 h-3.5" />
              </button>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <Field label="Agent persona" hint="Empty keeps the bot's agent.">
                {(props) => (
                  <Select
                    {...props}
                    value={row.profile.agent_persona ?? ""}
                    onChange={(e) => patchProfile(row, { agent_persona: e.target.value })}
                  >
                    <option value="">(Bot default)</option>
                    {agentOptions.map((agent) => (
                      <option key={agent.value} value={agent.value}>
                        {agent.label}
                      </option>
                    ))}
                  </Select>
                )}
              </Field>

              <Field label="Response mode">
                {(props) => (
                  <Select
                    {...props}
                    value={row.profile.response_mode ?? ""}
                    onChange={(e) => patchProfile(row, { response_mode: e.target.value as ChannelProfile["response_mode"] })}
                  >
                    <option value="">(Bot default)</option>
                    <option value="thread">Reply in thread</option>
                    <option value="private">Private</option>
                  </Select>
                )}
              </Field>

              <Field label="Sandbox image" hint="Must be an image the organization permits (Settings → Sandboxes).">
                {(props) => (
                  <Select
                    {...props}
                    value={row.profile.sandbox_image ?? ""}
                    onChange={(e) => patchProfile(row, { sandbox_image: e.target.value })}
                  >
                    <option value="">(Default image)</option>
                    {!imageKnown && <option value={row.profile.sandbox_image}>{row.profile.sandbox_image}</option>}
                    {images.map((image) => (
                      <option key={image.value} value={image.value}>
                        {image.label}
                      </option>
                    ))}
                  </Select>
                )}
              </Field>

              <Field label="Allowed tools" hint="Comma-separated tool names. Empty inherits the organization's scope.">
                {(props) => (
                  <Input
                    {...props}
                    list={`channel-tools-${row.key}`}
                    value={tools}
                    onChange={(e) => {
                      setToolDrafts({ ...toolDrafts, [row.key]: e.target.value });
                      patchProfile(row, { allowed_tools: splitTools(e.target.value) });
                    }}
                    placeholder="file_read, grep_search, web_search"
                  />
                )}
              </Field>
              <datalist id={`channel-tools-${row.key}`}>
                {toolNames.map((name) => (
                  <option key={name} value={name} />
                ))}
              </datalist>
            </div>

            <Field
              label="House rules"
              hint={`${(row.profile.instructions ?? "").length}/${INSTRUCTIONS_LIMIT} characters`}
            >
              {(props) => (
                <Textarea
                  {...props}
                  rows={3}
                  maxLength={INSTRUCTIONS_LIMIT}
                  value={row.profile.instructions ?? ""}
                  onChange={(e) => patchProfile(row, { instructions: e.target.value })}
                  placeholder="Answer in French. Never run commands against production."
                />
              )}
            </Field>
          </fieldset>
        );
      })}

      <button
        type="button"
        onClick={addRow}
        data-testid="add-channel-profile"
        className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-border text-xs text-foreground hover:bg-surface-elevated"
      >
        <Plus className="w-3.5 h-3.5" />
        <span>Add channel profile</span>
      </button>
    </div>
  );
}
