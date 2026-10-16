"use client";

import React, { useCallback, useEffect, useState } from "react";
import {
  CheckCircle2,
  AlertCircle,
  MessageSquare,
  RefreshCw,
  Radio,
  Plus,
  Bot,
  Layers,
  Edit2,
  Trash2,
  Lock,
  Globe,
} from "lucide-react";
import { SchemaSettingsForm } from "../../../components/settings/SchemaSettingsForm";
import {
  UserScopeSection,
  ScopeDivider,
  OrgScopeSection,
} from "../../../components/settings/ScopedSettingsSection";
import { useSession } from "../../../components/AuthProvider";
import { BotEditor } from "./BotEditor";
import { SlackBot } from "./types";

interface SlackStatusData {
  socket_mode_active: boolean;
  connected: boolean;
  channels_linked: number;
  channels: string[];
  team_id?: string | null;
  bot_user_id?: string | null;
  is_configured?: boolean;
}

interface WorkspaceBinding {
  connected_team_id: string | null;
  bound_org_id: string | null;
  bound_to_this_org: boolean;
}

function SlackLinkPanel() {
  const [code, setCode] = useState("");
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  const link = async () => {
    setMessage(null);
    const res = await fetch("/v1/slack/pair", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code: code.trim() }),
    });
    if (res.ok) {
      setCode("");
      setMessage({ ok: true, text: "Slack account linked. Your DMs with the bot now run as you." });
    } else {
      const body = await res.json().catch(() => ({}));
      setMessage({ ok: false, text: body.detail || `Linking failed (HTTP ${res.status})` });
    }
  };

  return (
    <div className="space-y-3" data-testid="slack-link-panel">
      <p className="text-xs text-muted-foreground">
        In Slack run the bot&apos;s command with <code>pair</code> (for example <code>/rocky pair</code>), then enter the one-time code it shows you (valid 10 minutes).
      </p>
      <div className="flex gap-2 max-w-md">
        <input
          data-testid="slack-pair-code"
          value={code}
          onChange={(e) => setCode(e.target.value.toUpperCase())}
          placeholder="ABCD2345"
          maxLength={16}
          className="flex-1 px-3 py-2 rounded-lg bg-surface-subnav border border-surface-border text-xs text-foreground focus:outline-none focus:border-brand"
        />
        <button
          type="button"
          data-testid="slack-pair-submit"
          onClick={link}
          disabled={!code.trim()}
          className="px-4 py-2 rounded-lg bg-brand text-white text-xs font-semibold disabled:opacity-50"
        >
          Link account
        </button>
      </div>
      {message && (
        <p role="status" className={`text-xs ${message.ok ? "text-emerald-400" : "text-rose-400"}`}>
          {message.text}
        </p>
      )}
    </div>
  );
}

function WorkspaceBindingPanel() {
  const [binding, setBinding] = useState<WorkspaceBinding | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const res = await fetch("/v1/admin/slack/workspace");
    if (res.ok) setBinding(await res.json());
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const change = async (method: "PUT" | "DELETE") => {
    setError(null);
    const res = await fetch("/v1/admin/slack/workspace", { method });
    if (res.ok) setBinding(await res.json());
    else setError((await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`);
  };

  if (!binding) return null;
  return (
    <div className="p-3 rounded-lg border border-border bg-surface-card space-y-2" data-testid="slack-workspace-binding">
      <p className="text-xs text-foreground">
        Connected workspace: <strong>{binding.connected_team_id ?? "none"}</strong>
        {" · "}
        {binding.bound_to_this_org
          ? "owned by this organization"
          : binding.bound_org_id
            ? "owned by another organization"
            : "not bound (uses the default organization)"}
      </p>
      <div className="flex gap-2">
        {!binding.bound_to_this_org && binding.connected_team_id && (
          <button
            type="button"
            onClick={() => change("PUT")}
            className="px-3 py-1.5 rounded-lg border border-border text-xs text-foreground"
          >
            Bind to this organization
          </button>
        )}
        {binding.bound_to_this_org && (
          <button
            type="button"
            onClick={() => change("DELETE")}
            className="px-3 py-1.5 rounded-lg border border-border text-xs text-foreground"
          >
            Unbind
          </button>
        )}
      </div>
      {error && <p className="text-xs text-rose-400">{error}</p>}
    </div>
  );
}

export default function SlackSettingsPage() {
  const { data: session } = useSession();
  const roles = (session?.user as unknown as { roles?: string[] })?.roles || [];
  // Bots are the organization's Slack apps: only administrators see or manage them.
  const isAdmin = !session || roles.includes("admin") || roles.includes("owner");
  const [testingSocket, setTestingSocket] = useState(false);
  const [successBanner, setSuccessBanner] = useState<string | null>(null);
  const [errorBanner, setErrorBanner] = useState<string | null>(null);
  const [slackStatus, setSlackStatus] = useState<SlackStatusData>({
    socket_mode_active: false,
    connected: false,
    channels_linked: 0,
    channels: [],
  });
  const [agentPersonas, setAgentPersonas] = useState<{ value: string; label: string }[]>([]);

  // Multi-bot state
  const [bots, setBots] = useState<SlackBot[]>([]);
  const [loadingBots, setLoadingBots] = useState(false);
  const [editingBot, setEditingBot] = useState<SlackBot | null>(null);
  const [isEditorOpen, setIsEditorOpen] = useState(false);

  const fetchStatus = useCallback(async () => {
    try {
      const res = await fetch("/v1/slack/status");
      if (res.ok) setSlackStatus(await res.json());
    } catch (err) {
      console.warn("Failed to read Slack status:", err);
    }
  }, []);

  const fetchBots = useCallback(async () => {
    if (!isAdmin) return;
    setLoadingBots(true);
    try {
      const res = await fetch("/v1/admin/slack/bots");
      if (res.ok) {
        setBots(await res.json());
      }
    } catch (err) {
      console.warn("Failed to load Slack bots:", err);
    } finally {
      setLoadingBots(false);
    }
  }, [isAdmin]);

  useEffect(() => {
    fetchStatus();
    fetchBots();
    fetch("/v1/agents")
      .then((res) => (res.ok ? res.json() : []))
      .then((agents: { id: string; name: string }[]) =>
        setAgentPersonas(agents.map((a) => ({ value: a.id, label: a.name }))),
      )
      .catch(() => {});
  }, [fetchStatus, fetchBots]);

  const handleTestSocketMode = async () => {
    setTestingSocket(true);
    setErrorBanner(null);
    try {
      const res = await fetch("/v1/slack/test", { method: "POST" });
      const data = await res.json();
      if (!res.ok || (!data.ok && !data.success)) {
        throw new Error(data.message || data.detail || "Slack auth test failed");
      }
      setSuccessBanner(`Socket Mode Connected: ${data.team || "Workspace"} as @${data.user || "bot"}`);
      fetchStatus();
      setTimeout(() => setSuccessBanner(null), 4000);
    } catch (err) {
      setErrorBanner(err instanceof Error ? err.message : "Slack test failed");
      setTimeout(() => setErrorBanner(null), 5000);
    } finally {
      setTestingSocket(false);
    }
  };

  const handleToggleBotEnabled = async (botId: string, currentEnabled: boolean) => {
    try {
      const res = await fetch(`/v1/admin/slack/bots/${botId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: !currentEnabled }),
      });
      if (res.ok) {
        const updated = await res.json();
        setBots(bots.map((b) => (b.id === botId ? updated : b)));
      }
    } catch (err) {
      console.warn("Failed to toggle bot enabled:", err);
    }
  };

  const handleDeleteBot = async (botId: string, botName: string) => {
    if (!confirm(`Are you sure you want to delete Slack bot "${botName}"?`)) return;
    try {
      const res = await fetch(`/v1/admin/slack/bots/${botId}`, { method: "DELETE" });
      if (res.ok) {
        setBots(bots.filter((b) => b.id !== botId));
      }
    } catch (err) {
      console.warn("Failed to delete bot:", err);
    }
  };

  const handleNewBot = () => {
    setEditingBot(null);
    setIsEditorOpen(true);
  };

  const handleEditBot = (bot: SlackBot) => {
    setEditingBot(bot);
    setIsEditorOpen(true);
  };

  const handleBotSaved = (saved: SlackBot) => {
    setIsEditorOpen(false);
    setEditingBot(null);
    const existing = bots.find((b) => b.id === saved.id);
    if (existing) {
      setBots(bots.map((b) => (b.id === saved.id ? saved : b)));
    } else {
      setBots([...bots, saved]);
    }
    fetchStatus();
  };

  return (
    <div className="max-w-5xl mx-auto space-y-6 px-4 py-8 sm:px-8">
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 pb-4 border-b border-border/70">
        <div>
          <h1 className="font-display text-xl font-semibold tracking-tight text-foreground flex items-center gap-2.5">
            <MessageSquare className="w-5 h-5 text-brand" />
            Slack &amp; chat ops
          </h1>
          <p className="max-w-2xl text-sm leading-relaxed text-neutral-600 dark:text-neutral-400 mt-1">
            Configure multiple Slack bots, response modes, message routing, and workspace policies.
          </p>
        </div>
      </div>

      {errorBanner && (
        <div className="flex items-center gap-2 p-3 rounded-lg bg-rose-950/60 border border-rose-800/60 text-rose-400 text-xs">
          <AlertCircle className="w-4 h-4 shrink-0" />
          <span>{errorBanner}</span>
        </div>
      )}
      {successBanner && (
        <div className="flex items-center gap-2 p-3 rounded-lg bg-emerald-950/60 border border-emerald-800/60 text-emerald-400 text-xs">
          <CheckCircle2 className="w-4 h-4 shrink-0" />
          <span>{successBanner}</span>
        </div>
      )}

      {/* Member Linking */}
      <UserScopeSection
        title="Link your Slack account"
        description="Pair your Slack user with this Rocket Chat account so direct messages run as you."
      >
        <SlackLinkPanel />
      </UserScopeSection>

      {/* User Notifications */}
      <UserScopeSection
        title="Slack Notifications"
        description="Whether the bot posts a completion notice in your thread when a long-running turn finishes."
      >
        <SchemaSettingsForm
          domain="user_preferences"
          mode="user"
          title="Notifications"
          only={["slack_notifications_enabled"]}
        />
      </UserScopeSection>

      <ScopeDivider label="Organization Scope" />
      <p className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
        Organization · managed by administrators
      </p>

      <OrgScopeSection
        title="Slack bots"
        description="The organization's Slack apps: each bot (@handle) answers with its own agent or router, response mode and policy, for everyone in the workspace."
      >
        <div className="space-y-4">
          {isAdmin ? (
            <>
            <div className="flex items-center justify-between pb-2">
              <div className="flex items-center gap-2">
                <span className="text-xs font-semibold text-foreground">
                  {bots.length} {bots.length === 1 ? "bot" : "bots"} configured
                </span>
              </div>
              <button
                type="button"
                onClick={handleNewBot}
                data-testid="new-bot-button"
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-brand text-white text-xs font-semibold hover:bg-brand/90 transition-colors"
              >
                <Plus className="w-3.5 h-3.5" />
                <span>New bot</span>
              </button>
            </div>

            {/* Bots Cards List */}
            {loadingBots ? (
              <div className="p-8 text-center text-xs text-muted-foreground">Loading bots...</div>
            ) : bots.length === 0 ? (
              <div className="p-8 text-center border border-dashed border-border rounded-xl space-y-2 bg-surface-card/40">
                <Bot className="w-8 h-8 text-muted-foreground mx-auto" />
                <p className="text-xs font-medium text-foreground">No Slack bots configured yet</p>
                <p className="text-[11px] text-muted-foreground max-w-sm mx-auto">
                  Add a bot to connect a Slack app handle, choose its agent or router, and pick whether it responds publicly or
                  privately.
                </p>
              </div>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4" data-testid="slack-bots-list">
                {bots.map((b) => (
                  <div
                    key={b.id}
                    data-testid={`slack-bot-card-${b.id}`}
                    className="p-4 rounded-xl border border-border bg-surface-card flex flex-col justify-between space-y-4 hover:border-neutral-400/60 transition-colors"
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="space-y-1">
                        <div className="flex items-center gap-2">
                          <span className="font-semibold text-sm text-foreground">{b.name}</span>
                          {b.handle && (
                            <span className="text-xs text-brand font-medium">@{b.handle}</span>
                          )}
                        </div>
                        <p className="text-[11px] text-muted-foreground">
                          {b.team_name ? `Workspace: ${b.team_name}` : "Slack App connection"}
                        </p>
                      </div>

                      <button
                        type="button"
                        role="switch"
                        aria-checked={b.enabled}
                        aria-label={`Toggle ${b.name}`}
                        onClick={() => handleToggleBotEnabled(b.id, b.enabled)}
                        className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none ${
                          b.enabled ? "bg-brand" : "bg-neutral-600 dark:bg-neutral-700"
                        }`}
                      >
                        <span
                          className={`pointer-events-none inline-block h-4 w-4 transform rounded-full bg-white shadow-lg ring-0 transition duration-200 ease-in-out ${
                            b.enabled ? "translate-x-4" : "translate-x-0"
                          }`}
                        />
                      </button>
                    </div>

                    <div className="flex flex-wrap items-center gap-2 text-[10px]">
                      {/* Brain Badge */}
                      {b.router ? (
                        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-purple-500/10 text-purple-400 border border-purple-500/20 font-medium">
                          <Layers className="w-3 h-3" />
                          <span>Router ({b.router.routes?.length || 0} routes)</span>
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-cyan-500/10 text-cyan-400 border border-cyan-500/20 font-medium">
                          <Bot className="w-3 h-3" />
                          <span>One Agent</span>
                        </span>
                      )}

                      {/* Response Mode Badge */}
                      {b.response_mode === "private" ? (
                        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-amber-500/10 text-amber-400 border border-amber-500/20 font-medium">
                          <Lock className="w-2.5 h-2.5" />
                          <span>Private session</span>
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 font-medium">
                          <Globe className="w-2.5 h-2.5" />
                          <span>Thread public</span>
                        </span>
                      )}
                    </div>

                    <div className="flex items-center justify-end gap-2 pt-2 border-t border-border/60">
                      <button
                        type="button"
                        onClick={() => handleEditBot(b)}
                        data-testid={`edit-bot-${b.id}`}
                        className="flex items-center gap-1 px-2.5 py-1 rounded-lg border border-border text-xs text-foreground hover:bg-surface-elevated transition-colors"
                      >
                        <Edit2 className="w-3 h-3" />
                        <span>Edit</span>
                      </button>
                      <button
                        type="button"
                        onClick={() => handleDeleteBot(b.id, b.name)}
                        className="p-1.5 text-muted-foreground hover:text-rose-400 rounded-lg hover:bg-surface-elevated transition-colors"
                        title="Delete bot"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
            </>
          ) : (
            <p data-testid="slack-bots-admin-only" className="text-xs text-muted-foreground">
              Slack bots are added and configured by your organization&apos;s administrators.
            </p>
          )}

          {/* Live Socket Status */}
          <div className="flex items-center justify-between p-3 rounded-lg border border-border bg-surface-card">
            <div className="flex items-center gap-2.5">
              <Radio
                className={`w-4 h-4 ${slackStatus.connected ? "text-emerald-400 animate-pulse" : "text-neutral-500"}`}
              />
              <div>
                <span className="text-xs font-semibold text-foreground">
                  {slackStatus.connected ? "Socket Mode Active" : "Socket Mode Offline / Standby"}
                </span>
                <p className="text-[10px] text-muted-foreground">
                  {slackStatus.channels_linked} Slack conversations seen since startup.
                </p>
              </div>
            </div>
            <button
              type="button"
              onClick={handleTestSocketMode}
              disabled={testingSocket}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-border bg-surface-elevated text-xs text-neutral-300 disabled:opacity-50"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${testingSocket ? "animate-spin" : ""}`} />
              <span>Test socket connection</span>
            </button>
          </div>

          {isAdmin && <WorkspaceBindingPanel />}

          <SchemaSettingsForm
            domain="slack"
            mode="org"
            title="Default organization policy"
            selectOptions={{ default_agent_persona: agentPersonas }}
          />
        </div>
      </OrgScopeSection>

      {/* Bot Editor Modal */}
      {isEditorOpen && (
        <BotEditor
          bot={editingBot}
          agentOptions={agentPersonas}
          onClose={() => setIsEditorOpen(false)}
          onSaved={handleBotSaved}
        />
      )}
    </div>
  );
}
