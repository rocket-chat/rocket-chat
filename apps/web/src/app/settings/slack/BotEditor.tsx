"use client";

import React from "react";
import { tabKeyHandler } from "../../../lib/tab-keys";
import { AlertCircle, Cpu, Save, X } from "lucide-react";
import type { SlackBot } from "./types";
import { ActivityTab } from "./editor/ActivityTab";
import { BehaviourTab } from "./editor/BehaviourTab";
import { ConnectionTab } from "./editor/ConnectionTab";
import { RoutingTab } from "./editor/RoutingTab";
import { useBotEditor, type TabKey } from "./editor/useBotEditor";

interface BotEditorProps {
  bot: SlackBot | null;
  agentOptions: { value: string; label: string }[];
  onClose: () => void;
  onSaved: (savedBot: SlackBot) => void;
}

const TABS: { id: TabKey; label: string }[] = [
  { id: "connection", label: "Connection" },
  { id: "behaviour", label: "Behaviour" },
  { id: "routing", label: "Routing" },
  { id: "activity", label: "Activity" },
];

/** A Slack bot in four tabs: how it connects, how it behaves, who answers what, and what it did. */
export function BotEditor({ bot, agentOptions, onClose, onSaved }: BotEditorProps) {
  const editor = useBotEditor(bot, agentOptions[0]?.value ?? "", onSaved);
  const { isNew, tab, setTab, saving, error } = editor;
  const tabs = TABS.filter((t) => t.id !== "activity" || !isNew);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="bot-editor-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm"
      data-testid="slack-bot-editor"
    >
      <div className="relative flex max-h-[90vh] w-full max-w-4xl flex-col overflow-hidden rounded-xl border border-border bg-surface-card shadow-2xl">
        <div className="flex items-center justify-between border-b border-border/70 bg-surface-subnav/50 px-6 py-4">
          <div>
            <h2 id="bot-editor-title" className="flex items-center gap-2 text-base font-semibold text-foreground">
              <Cpu className="h-4 w-4 text-brand" aria-hidden="true" />
              {bot ? `Edit Bot: ${bot.name}` : "New Slack Bot"}
            </h2>
            <p className="mt-0.5 text-xs text-muted-foreground">{bot?.handle ? `@${bot.handle}` : "Configure Slack app connection, routing and policy"}</p>
          </div>
          <button type="button" onClick={onClose} aria-label="Close" className="rounded-lg p-1 text-muted-foreground hover:bg-surface-elevated hover:text-foreground">
            <X className="h-5 w-5" aria-hidden="true" />
          </button>
        </div>

        <div role="tablist" aria-label="Bot settings" className="flex gap-2 overflow-x-auto border-b border-border bg-surface-subnav/20 px-6 text-xs">
          {tabs.map((t) => (
            <button
              key={t.id}
              type="button"
              role="tab"
              id={`bot-tab-${t.id}`}
              aria-selected={tab === t.id}
              aria-controls={`bot-panel-${t.id}`}
              tabIndex={tab === t.id ? 0 : -1}
              data-testid={`bot-tab-${t.id}`}
              onClick={() => setTab(t.id)}
              onKeyDown={tabKeyHandler(tabs.map((x) => x.id), tab, setTab, (id) => `bot-tab-${id}`)}
              className={`whitespace-nowrap border-b-2 px-3 py-3 font-medium transition-colors ${
                tab === t.id ? "border-brand font-semibold text-brand" : "border-transparent text-muted-foreground hover:text-foreground"
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>

        <div role="tabpanel" id={`bot-panel-${tab}`} aria-labelledby={`bot-tab-${tab}`} className="flex-1 space-y-6 overflow-y-auto p-6">
          {error && (
            <div role="alert" data-testid="bot-editor-error" className="flex items-center gap-2 rounded-lg border border-rose-500/40 bg-rose-500/10 p-3 text-xs text-rose-700 dark:text-rose-300">
              <AlertCircle className="h-4 w-4 shrink-0" aria-hidden="true" />
              <span>{error}</span>
            </div>
          )}
          {tab === "connection" && <ConnectionTab editor={editor} />}
          {tab === "behaviour" && <BehaviourTab editor={editor} />}
          {tab === "routing" && <RoutingTab editor={editor} agentOptions={agentOptions} />}
          {tab === "activity" && bot && <ActivityTab botId={bot.id} />}
        </div>

        <div className="flex items-center justify-end gap-3 border-t border-border/70 bg-surface-subnav/50 px-6 py-3">
          <button type="button" onClick={onClose} className="rounded-lg border border-border px-4 py-2 text-xs text-foreground hover:bg-surface-elevated">
            Cancel
          </button>
          <button
            type="button"
            onClick={() => void editor.save()}
            disabled={saving}
            data-testid="save-bot-button"
            className="flex items-center gap-1.5 rounded-lg bg-brand px-4 py-2 text-xs font-semibold text-white hover:bg-brand/90 disabled:opacity-50"
          >
            <Save className="h-3.5 w-3.5" aria-hidden="true" />
            <span>{saving ? "Saving..." : isNew ? "Create bot" : "Save changes"}</span>
          </button>
        </div>
      </div>
    </div>
  );
}
