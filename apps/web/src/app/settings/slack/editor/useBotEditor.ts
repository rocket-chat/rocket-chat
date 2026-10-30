"use client";

import { useState } from "react";
import type { SlackBot } from "../types";
import {
  channelProfilesOf,
  classifierFromBot,
  routerOf,
  routingProblem,
  rowsFromBot,
  type ClassifierSettings,
  type RoutingRow,
} from "./routing";

export type TabKey = "connection" | "behaviour" | "routing" | "activity";

/** Everything the bot editor submits, in one object every tab reads and patches. */
export interface BotForm {
  name: string;
  slashCommand: string;
  botToken: string;
  appToken: string;
  responseMode: "thread" | "private";
  respondInDms: boolean;
  listenChannels: string;
  privateReaction: string;
  privateDmResult: boolean;
  privateDmUpdates: boolean;
  /** The bot's overrides of the organization's Slack policy, without the channel rows. */
  policy: Record<string, unknown>;
  rows: RoutingRow[];
  classifier: ClassifierSettings;
}

function formFromBot(bot: SlackBot | null, fallbackAgent: string): BotForm {
  const policy = { ...(bot?.policy ?? {}) };
  delete policy.channel_profiles;
  return {
    name: bot?.name ?? "",
    slashCommand: bot?.slash_command ?? "",
    botToken: "",
    appToken: "",
    responseMode: bot?.response_mode ?? "thread",
    respondInDms: bot?.respond_in_dms ?? true,
    listenChannels: bot?.listen_channels?.join(", ") ?? "",
    privateReaction: bot?.private_reaction || "white_check_mark",
    privateDmResult: bot?.private_dm_result ?? false,
    privateDmUpdates: bot?.private_dm_updates ?? false,
    policy,
    rows: rowsFromBot(bot, fallbackAgent),
    classifier: classifierFromBot(bot),
  };
}

function payloadOf(form: BotForm): Record<string, unknown> {
  const defaultAgent = form.rows.find((row) => row.kind === "default")?.agent_id ?? "";
  const profiles = channelProfilesOf(form.rows);
  const payload: Record<string, unknown> = {
    name: form.name.trim(),
    slash_command: form.slashCommand.trim() || null,
    agent_id: defaultAgent || null,
    response_mode: form.responseMode,
    respond_in_dms: form.respondInDms,
    listen_channels: form.listenChannels.split(",").map((c) => c.trim()).filter(Boolean),
    private_reaction: form.privateReaction.trim() || "white_check_mark",
    private_dm_result: form.privateDmResult,
    private_dm_updates: form.privateDmUpdates,
    router: routerOf(form.rows, form.classifier, defaultAgent),
    policy: Object.keys(profiles).length > 0 ? { ...form.policy, channel_profiles: profiles } : form.policy,
  };
  if (form.botToken.trim()) payload.bot_token = form.botToken.trim();
  if (form.appToken.trim()) payload.app_token = form.appToken.trim();
  return payload;
}

/** The bot editor's state (one form object), the open tab, and saving. */
export function useBotEditor(bot: SlackBot | null, fallbackAgent: string, onSaved: (bot: SlackBot) => void) {
  const isNew = !bot;
  const [tab, setTab] = useState<TabKey>("connection");
  const [form, setForm] = useState<BotForm>(() => formFromBot(bot, fallbackAgent));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const patch = (changes: Partial<BotForm>) => setForm((current) => ({ ...current, ...changes }));

  const problemBeforeSave = (): { message: string; tab: TabKey } | null => {
    if (!form.name.trim()) return { message: "Please enter a name for this bot.", tab: "connection" };
    if (isNew && (!form.botToken.trim() || !form.appToken.trim())) {
      return { message: "Both Bot Token (xoxb-...) and App Token (xapp-...) are required.", tab: "connection" };
    }
    const routing = routingProblem(form.rows);
    return routing ? { message: routing, tab: "routing" } : null;
  };

  const save = async () => {
    const problem = problemBeforeSave();
    setError(problem?.message ?? null);
    if (problem) {
      setTab(problem.tab);
      return;
    }
    setSaving(true);
    try {
      const res = await fetch(isNew ? "/v1/admin/slack/bots" : `/v1/admin/slack/bots/${bot.id}`, {
        method: isNew ? "POST" : "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payloadOf(form)),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Failed to save bot.");
      onSaved(data as SlackBot);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error saving bot.");
    } finally {
      setSaving(false);
    }
  };

  return { bot, isNew, tab, setTab, form, patch, saving, error, setError, save };
}

export type BotEditorState = ReturnType<typeof useBotEditor>;
