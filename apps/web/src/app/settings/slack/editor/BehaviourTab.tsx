"use client";

import React from "react";
import { Field, Input, Select } from "../../../../components/ui/Field";
import { InheritanceBadge } from "../../../../components/settings/InheritanceBadge";
import type { BotEditorState } from "./useBotEditor";

const CHECKBOX = "rounded border-border text-brand focus:ring-brand";

const PrivateModeSettings: React.FC<{ editor: BotEditorState }> = ({ editor }) => {
  const { form, patch } = editor;
  return (
    <div className="space-y-3 rounded-lg border border-border bg-surface-subnav/30 p-3.5">
      <span className="text-xs font-semibold text-foreground">Private Mode Settings</span>
      <Field label="Acknowledgement Reaction" hint="Slack emoji name added to user's message when taken.">
        {(props) => (
          <Input {...props} value={form.privateReaction} onChange={(e) => patch({ privateReaction: e.target.value })} placeholder="white_check_mark" />
        )}
      </Field>
      <label className="flex cursor-pointer items-center gap-2.5 text-xs text-foreground">
        <input type="checkbox" checked={form.privateDmResult} onChange={(e) => patch({ privateDmResult: e.target.checked })} className={CHECKBOX} />
        <span>Also send completion summary as a direct message (DM)</span>
      </label>
      <label className="flex cursor-pointer items-center gap-2.5 text-xs text-foreground">
        <input
          type="checkbox"
          data-testid="private-dm-updates"
          checked={form.privateDmUpdates}
          onChange={(e) => patch({ privateDmUpdates: e.target.checked })}
          className={CHECKBOX}
        />
        <span>Send questions, approvals, errors and the result by direct message, with a link to the thread</span>
      </label>
    </div>
  );
};

const PolicyOverrides: React.FC<{ editor: BotEditorState }> = ({ editor }) => {
  const { bot, form, patch } = editor;
  const overridden = Object.keys(form.policy).length > 0;
  const source = `slack_bot:${bot?.id ?? "new"}`;
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between border-b border-border/60 pb-2">
        <span className="text-xs font-semibold text-foreground">Policy for this bot (a key the organization locked keeps its value)</span>
        <span className="flex items-center gap-2">
          <InheritanceBadge provenance={{ source: overridden ? source : "org", locked_by: null }} presetNames={{ [source]: form.name || "this bot" }} />
          {overridden && (
            <button type="button" onClick={() => patch({ policy: {} })} className="text-[10px] text-neutral-500 hover:text-foreground">
              Use the organization&apos;s
            </button>
          )}
        </span>
      </div>
      <Field label="DM Policy" hint="Who may message this bot directly.">
        {(props) => (
          <Select {...props} value={String(form.policy.dm_policy || "")} onChange={(e) => patch({ policy: { ...form.policy, dm_policy: e.target.value } })}>
            <option value="">(Inherits organization policy)</option>
            <option value="all">All workspace users</option>
            <option value="paired">Paired members only</option>
            <option value="none">Disabled</option>
          </Select>
        )}
      </Field>
      <Field label="Outbound Channels" hint="Comma-separated allowlist of channels this bot may post in.">
        {(props) => (
          <Input
            {...props}
            value={String(form.policy.outbound_channels || "")}
            onChange={(e) => patch({ policy: { ...form.policy, outbound_channels: e.target.value } })}
            placeholder="Leave empty to inherit organization default"
          />
        )}
      </Field>
    </div>
  );
};

/** How the bot answers: response mode, triggers, private-mode details and policy overrides. */
export const BehaviourTab: React.FC<{ editor: BotEditorState }> = ({ editor }) => {
  const { form, patch } = editor;
  return (
    <div className="space-y-6">
      <div className="space-y-5">
        <Field label="Default Response Mode" hint="Choose how this bot answers when invoked in a channel.">
          {(props) => (
            <Select
              {...props}
              data-testid="bot-response-mode-select"
              value={form.responseMode}
              onChange={(e) => patch({ responseMode: e.target.value === "private" ? "private" : "thread" })}
            >
              <option value="thread">Thread (publicly in the Slack thread)</option>
              <option value="private">Private session (ephemeral link &amp; private summary)</option>
            </Select>
          )}
        </Field>
        <div className="space-y-3 rounded-lg border border-border bg-surface-subnav/30 p-3.5">
          <span className="text-xs font-semibold text-foreground">Triggers &amp; Channels</span>
          <label className="flex cursor-pointer items-center gap-2.5 text-xs text-foreground">
            <input type="checkbox" checked={form.respondInDms} onChange={(e) => patch({ respondInDms: e.target.checked })} className={CHECKBOX} />
            <span>Respond to direct messages (DMs) with this bot</span>
          </label>
          <Field
            label="Listen without mention in channels (optional)"
            hint="Comma-separated channel IDs (e.g. C01234ABC, C05678DEF) where this bot routes every message."
          >
            {(props) => (
              <Input {...props} value={form.listenChannels} onChange={(e) => patch({ listenChannels: e.target.value })} placeholder="C01234ABC, C05678DEF" />
            )}
          </Field>
        </div>
        {form.responseMode === "private" && <PrivateModeSettings editor={editor} />}
      </div>
      <PolicyOverrides editor={editor} />
    </div>
  );
};
