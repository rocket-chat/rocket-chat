"use client";

import React, { useCallback, useEffect, useState } from "react";
import { CheckCircle2, AlertCircle, MessageSquare, RefreshCw, Radio } from "lucide-react";
import { SchemaSettingsForm } from "../../../components/settings/SchemaSettingsForm";
import {
  UserScopeSection,
  ScopeDivider,
  OrgScopeSection,
} from "../../../components/settings/ScopedSettingsSection";

interface SlackStatusData {
  socket_mode_active: boolean;
  connected: boolean;
  channels_linked: number;
  channels: string[];
  team_id?: string | null;
  bot_user_id?: string | null;
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
      <p className="text-xs font-mono text-muted-foreground">
        In Slack run <code>/rocket-pair</code>, then enter the one-time code it shows you (valid 10 minutes).
      </p>
      <div className="flex gap-2 max-w-md">
        <input
          data-testid="slack-pair-code"
          value={code}
          onChange={(e) => setCode(e.target.value.toUpperCase())}
          placeholder="ABCD2345"
          maxLength={16}
          className="flex-1 px-3 py-2 rounded-lg bg-surface-subnav border border-surface-border text-xs font-mono text-foreground focus:outline-none focus:border-brand"
        />
        <button
          type="button"
          data-testid="slack-pair-submit"
          onClick={link}
          disabled={!code.trim()}
          className="px-4 py-2 rounded-lg bg-brand text-white text-xs font-mono font-semibold disabled:opacity-50"
        >
          Link account
        </button>
      </div>
      {message && (
        <p role="status" className={`text-xs font-mono ${message.ok ? "text-emerald-400" : "text-rose-400"}`}>
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
      <p className="text-xs font-mono text-foreground">
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
            className="px-3 py-1.5 rounded border border-border text-xs font-mono text-foreground"
          >
            Bind to this organization
          </button>
        )}
        {binding.bound_to_this_org && (
          <button
            type="button"
            onClick={() => change("DELETE")}
            className="px-3 py-1.5 rounded border border-border text-xs font-mono text-foreground"
          >
            Unbind
          </button>
        )}
      </div>
      {error && <p className="text-xs font-mono text-rose-400">{error}</p>}
    </div>
  );
}

export default function SlackSettingsPage() {
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

  const fetchStatus = useCallback(async () => {
    try {
      const res = await fetch("/v1/slack/status");
      if (res.ok) setSlackStatus(await res.json());
    } catch (err) {
      console.warn("Failed to read Slack status:", err);
    }
  }, []);

  useEffect(() => {
    fetchStatus();
    fetch("/v1/agents")
      .then((res) => (res.ok ? res.json() : []))
      .then((agents: { id: string; name: string }[]) =>
        setAgentPersonas(agents.map((a) => ({ value: a.id, label: a.name }))),
      )
      .catch(() => {});
  }, [fetchStatus]);

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

  return (
    <div className="max-w-5xl mx-auto space-y-6 px-4 py-8 sm:px-8">
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 pb-4 border-b border-border/70">
        <div>
          <h1 className="font-display text-xl font-semibold tracking-tight text-foreground flex items-center gap-2.5">
            <MessageSquare className="w-5 h-5 text-brand" />
            Slack &amp; chat ops
          </h1>
          <p className="max-w-2xl text-sm leading-relaxed text-neutral-600 dark:text-neutral-400 mt-1">
            Slack notifications, the organization bot policy, and the live Socket Mode connection.
          </p>
        </div>
      </div>

      {errorBanner && (
        <div className="flex items-center gap-2 p-3 rounded-lg bg-rose-950/60 border border-rose-800/60 text-rose-400 text-xs font-mono">
          <AlertCircle className="w-4 h-4 shrink-0" />
          <span>{errorBanner}</span>
        </div>
      )}
      {successBanner && (
        <div className="flex items-center gap-2 p-3 rounded-lg bg-emerald-950/60 border border-emerald-800/60 text-emerald-400 text-xs font-mono">
          <CheckCircle2 className="w-4 h-4 shrink-0" />
          <span>{successBanner}</span>
        </div>
      )}

      <UserScopeSection
        title="Link your Slack account"
        description="Pair your Slack user with this Rocket Chat account so direct messages run as you."
      >
        <SlackLinkPanel />
      </UserScopeSection>

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

      <OrgScopeSection
        title="Organization Slack Bot & Socket Integration"
        description="Workspace policy: where the bot answers, which agent and tools it uses, and who it may message."
      >
        <div className="space-y-4">
          <div className="flex items-center justify-between p-3 rounded-lg border border-border bg-surface-card">
            <div className="flex items-center gap-2.5">
              <Radio
                className={`w-4 h-4 ${slackStatus.connected ? "text-emerald-400 animate-pulse" : "text-neutral-500"}`}
              />
              <div>
                <span className="text-xs font-mono font-semibold text-foreground">
                  {slackStatus.connected ? "Socket Mode Active" : "Socket Mode Offline / Standby"}
                </span>
                <p className="text-[10px] font-mono text-muted-foreground">
                  {slackStatus.channels_linked} Slack conversations seen since startup.
                </p>
              </div>
            </div>
            <button
              type="button"
              onClick={handleTestSocketMode}
              disabled={testingSocket}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded border border-border bg-surface-elevated text-xs font-mono text-neutral-300 disabled:opacity-50"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${testingSocket ? "animate-spin" : ""}`} />
              <span>TEST SOCKET</span>
            </button>
          </div>

          <WorkspaceBindingPanel />

          <SchemaSettingsForm
            domain="slack"
            mode="org"
            title="Slack bot policy"
            selectOptions={{ default_agent_persona: agentPersonas }}
          />
        </div>
      </OrgScopeSection>
    </div>
  );
}
