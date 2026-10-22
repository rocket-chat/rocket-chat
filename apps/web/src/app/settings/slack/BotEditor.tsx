"use client";

import React, { useState, useEffect, useCallback } from "react";
import {
  X,
  Save,
  AlertCircle,
  Copy,
  Download,
  Play,
  Plus,
  Trash2,
  RefreshCw,
  Cpu,
  Layers,
  Sparkles,
} from "lucide-react";
import { Field, Input, Select, Textarea } from "../../../components/ui/Field";
import { ModelPicker } from "../../../components/ui/ModelPicker";
import { InheritanceBadge } from "../../../components/settings/InheritanceBadge";
import { SlackBot, SlackRoute, RouteDecisionItem, ChannelProfile } from "./types";
import { ChannelProfilesEditor } from "./ChannelProfilesEditor";

interface BotEditorProps {
  bot: SlackBot | null;
  agentOptions: { value: string; label: string }[];
  onClose: () => void;
  onSaved: (savedBot: SlackBot) => void;
}

type TabKey = "connection" | "behaviour" | "brain" | "router" | "channels" | "policy" | "activity";

export function BotEditor({ bot, agentOptions, onClose, onSaved }: BotEditorProps) {
  const isNew = !bot;
  const [activeTab, setActiveTab] = useState<TabKey>("connection");

  // Form state
  const [name, setName] = useState(bot?.name || "");
  const [slashCommand, setSlashCommand] = useState(bot?.slash_command || "");
  const [botToken, setBotToken] = useState("");
  const [appToken, setAppToken] = useState("");
  const [agentId, setAgentId] = useState(bot?.agent_id || (agentOptions[0]?.value || ""));
  const [responseMode, setResponseMode] = useState<"thread" | "private">(bot?.response_mode || "thread");
  const [respondInDms, setRespondInDms] = useState(bot?.respond_in_dms ?? true);
  const [listenChannels, setListenChannels] = useState(bot?.listen_channels?.join(", ") || "");
  const [privateReaction, setPrivateReaction] = useState(bot?.private_reaction || "white_check_mark");
  const [privateDmResult, setPrivateDmResult] = useState(bot?.private_dm_result ?? false);
  const [privateDmUpdates, setPrivateDmUpdates] = useState(bot?.private_dm_updates ?? false);
  const [brainType, setBrainType] = useState<"agent" | "router">(bot?.router ? "router" : "agent");

  // Router state
  const [classifierAgentId, setClassifierAgentId] = useState(bot?.router?.classifier_agent_id || (agentOptions[0]?.value || ""));
  const [classifierModel, setClassifierModel] = useState(bot?.router?.classifier_model || "");
  const [routes, setRoutes] = useState<SlackRoute[]>(bot?.router?.routes || [
    { name: "general", description: "General engineering and coding questions", agent_id: agentOptions[0]?.value || "", examples: [], response_mode: "", enabled: true },
  ]);
  const [allowDismiss, setAllowDismiss] = useState(bot?.router?.allow_dismiss ?? true);
  const [dismissDescription, setDismissDescription] = useState(bot?.router?.dismiss_description || "Casual chit-chat, spam, or out of scope requests");
  const [minConfidence, setMinConfidence] = useState(bot?.router?.min_confidence ?? 0.6);
  const [fallback, setFallback] = useState(bot?.router?.fallback || "");
  const [sticky, setSticky] = useState(bot?.router?.sticky ?? true);

  // Policy overrides
  const [policyOverrides, setPolicyOverrides] = useState<Record<string, unknown>>(bot?.policy || {});

  // Test / Manifest / Decisions state
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string } | null>(null);
  const [testing, setTesting] = useState(false);
  const [manifestData, setManifestData] = useState<string | null>(null);
  const [loadingManifest, setLoadingManifest] = useState(false);
  const [tryText, setTryText] = useState("");
  const [tryExcerpt, setTryExcerpt] = useState("");
  const [tryResult, setTryResult] = useState<Record<string, unknown> | null>(null);
  const [trying, setTrying] = useState(false);
  const [decisions, setDecisions] = useState<RouteDecisionItem[]>([]);
  const [loadingDecisions, setLoadingDecisions] = useState(false);

  // Form messaging
  const [saving, setSaving] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const loadManifest = useCallback(async () => {
    if (!bot?.id) return;
    setLoadingManifest(true);
    try {
      const res = await fetch(`/v1/admin/slack/bots/${bot.id}/manifest`);
      if (res.ok) {
        const data = await res.json();
        setManifestData(JSON.stringify(data, null, 2));
      }
    } catch {
      // Manifest load failed silently
    } finally {
      setLoadingManifest(false);
    }
  }, [bot?.id]);

  const loadDecisions = useCallback(async () => {
    if (!bot?.id) return;
    setLoadingDecisions(true);
    try {
      const res = await fetch(`/v1/admin/slack/bots/${bot.id}/decisions?limit=50`);
      if (res.ok) {
        setDecisions(await res.json());
      }
    } catch {
      // Decisions load failed silently
    } finally {
      setLoadingDecisions(false);
    }
  }, [bot?.id]);

  useEffect(() => {
    if (activeTab === "connection" && bot?.id) {
      loadManifest();
    }
    if (activeTab === "activity" && bot?.id) {
      loadDecisions();
    }
  }, [activeTab, bot?.id, loadManifest, loadDecisions]);

  const handleTestConnection = async () => {
    if (!bot?.id) return;
    setTesting(true);
    setTestResult(null);
    try {
      const res = await fetch(`/v1/admin/slack/bots/${bot.id}/test`, { method: "POST" });
      const data = await res.json();
      if (res.ok && data.ok) {
        setTestResult({ ok: true, message: `Connected to workspace "${data.team_name}" as @${data.handle}` });
      } else {
        setTestResult({ ok: false, message: data.detail || "Connection test failed." });
      }
    } catch (err) {
      setTestResult({ ok: false, message: err instanceof Error ? err.message : "Network error" });
    } finally {
      setTesting(false);
    }
  };

  const handleTryRouter = async () => {
    if (!bot?.id || !tryText.trim()) return;
    setTrying(true);
    setTryResult(null);
    try {
      const res = await fetch(`/v1/admin/slack/bots/${bot.id}/router/try`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: tryText.trim(), thread_excerpt: tryExcerpt.trim() }),
      });
      const data = await res.json();
      if (res.ok) {
        setTryResult(data);
      } else {
        setErrorMessage(data.detail || "Router test failed");
      }
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : "Network error");
    } finally {
      setTrying(false);
    }
  };

  const handleSave = async () => {
    setErrorMessage(null);
    if (!name.trim()) {
      setErrorMessage("Please enter a name for this bot.");
      setActiveTab("connection");
      return;
    }
    if (isNew && (!botToken.trim() || !appToken.trim())) {
      setErrorMessage("Both Bot Token (xoxb-...) and App Token (xapp-...) are required.");
      setActiveTab("connection");
      return;
    }

    setSaving(true);
    const channels = listenChannels
      .split(",")
      .map((c) => c.trim())
      .filter(Boolean);

    const routerConfig =
      brainType === "router"
        ? {
            classifier_agent_id: classifierAgentId,
            classifier_model: classifierModel.trim() || undefined,
            routes: routes.filter((r) => r.name.trim()),
            allow_dismiss: allowDismiss,
            dismiss_description: dismissDescription,
            min_confidence: Number(minConfidence),
            fallback: fallback,
            sticky: sticky,
            context_messages: 5,
          }
        : null;

    const payload: Record<string, unknown> = {
      name: name.trim(),
      slash_command: slashCommand.trim() || null,
      agent_id: brainType === "agent" ? agentId : null,
      response_mode: responseMode,
      respond_in_dms: respondInDms,
      listen_channels: channels,
      private_reaction: privateReaction.trim() || "white_check_mark",
      private_dm_result: privateDmResult,
      private_dm_updates: privateDmUpdates,
      router: routerConfig,
      policy: policyOverrides,
    };

    if (botToken.trim()) payload.bot_token = botToken.trim();
    if (appToken.trim()) payload.app_token = appToken.trim();

    try {
      const url = isNew ? "/v1/admin/slack/bots" : `/v1/admin/slack/bots/${bot.id}`;
      const method = isNew ? "POST" : "PUT";
      const res = await fetch(url, {
        method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || "Failed to save bot.");
      }
      onSaved(data);
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : "Error saving bot.");
    } finally {
      setSaving(false);
    }
  };

  const addRoute = () => {
    setRoutes([
      ...routes,
      {
        name: `route_${routes.length + 1}`,
        description: "",
        agent_id: agentOptions[0]?.value || "",
        examples: [],
        response_mode: "",
        enabled: true,
      },
    ]);
  };

  const updateRoute = (index: number, updates: Partial<SlackRoute>) => {
    const updated = [...routes];
    updated[index] = { ...updated[index], ...updates };
    setRoutes(updated);
  };

  const removeRoute = (index: number) => {
    setRoutes(routes.filter((_, i) => i !== index));
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="bot-editor-title"
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm"
      data-testid="slack-bot-editor"
    >
      <div className="relative w-full max-w-4xl max-h-[90vh] flex flex-col bg-surface-card border border-border rounded-xl shadow-2xl overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-border/70 bg-surface-subnav/50">
          <div>
            <h2 id="bot-editor-title" className="text-base font-semibold text-foreground flex items-center gap-2">
              <Cpu className="w-4 h-4 text-brand" />
              {isNew ? "New Slack Bot" : `Edit Bot: ${bot.name}`}
            </h2>
            <p className="text-xs text-muted-foreground mt-0.5">
              {bot?.handle ? `@${bot.handle}` : "Configure Slack app connection, routing and policy"}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="p-1 rounded-lg text-muted-foreground hover:text-foreground hover:bg-surface-elevated"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        <div
          role="tablist"
          aria-label="Bot settings"
          className="flex border-b border-border bg-surface-subnav/20 px-6 gap-2 overflow-x-auto text-xs"
        >
          {[
            { id: "connection", label: "Connection" },
            { id: "behaviour", label: "Behaviour" },
            { id: "brain", label: "Brain" },
            ...(brainType === "router" ? [{ id: "router", label: "Router" }] : []),
            { id: "channels", label: "Channels" },
            { id: "policy", label: "Policy" },
            ...(!isNew ? [{ id: "activity", label: "Activity" }] : []),
          ].map((tab) => (
            <button
              key={tab.id}
              type="button"
              role="tab"
              aria-selected={activeTab === tab.id}
              onClick={() => setActiveTab(tab.id as TabKey)}
              className={`py-3 px-3 font-medium border-b-2 transition-colors whitespace-nowrap ${
                activeTab === tab.id
                  ? "border-brand text-brand font-semibold"
                  : "border-transparent text-muted-foreground hover:text-foreground"
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>

        {/* Body content */}
        <div className="flex-1 overflow-y-auto p-6 space-y-6">
          {errorMessage && (
            <div className="flex items-center gap-2 p-3 rounded-lg bg-rose-950/60 border border-rose-800/60 text-rose-400 text-xs">
              <AlertCircle className="w-4 h-4 shrink-0" />
              <span>{errorMessage}</span>
            </div>
          )}

          {/* TAB: Connection */}
          {activeTab === "connection" && (
            <div className="space-y-5">
              <Field label="Bot Display Name" hint="How this bot appears in Rocket Chat cockpit.">
                {(props) => (
                  <Input
                    {...props}
                    data-testid="bot-name-input"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="e.g. Senior Backend Assistant"
                  />
                )}
              </Field>
              <Field
                label="Slash command"
                hint={`Empty: named after the bot (${bot?.command || "from its Slack name"}). Each bot needs its own: Slack sends a command two apps share to the one installed last. Copy the manifest into the Slack app after changing it.`}
              >
                {(props) => (
                  <Input
                    {...props}
                    data-testid="bot-command-input"
                    value={slashCommand}
                    onChange={(e) => setSlashCommand(e.target.value)}
                    placeholder={bot?.command || "/rocky"}
                  />
                )}
              </Field>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <Field
                  label="Bot User OAuth Token"
                  hint={bot?.bot_token_fingerprint ? `Stored (${bot.bot_token_fingerprint})` : "Starts with xoxb-"}
                >
                  {(props) => (
                    <Input
                      {...props}
                      type="password"
                      data-testid="bot-token-input"
                      value={botToken}
                      onChange={(e) => setBotToken(e.target.value)}
                      placeholder={bot?.bot_token_fingerprint ? "•••••••• (unchanged)" : "xoxb-..."}
                    />
                  )}
                </Field>

                <Field
                  label="App-Level Token (Socket Mode)"
                  hint={bot?.app_token_fingerprint ? `Stored (${bot.app_token_fingerprint})` : "Starts with xapp-"}
                >
                  {(props) => (
                    <Input
                      {...props}
                      type="password"
                      data-testid="app-token-input"
                      value={appToken}
                      onChange={(e) => setAppToken(e.target.value)}
                      placeholder={bot?.app_token_fingerprint ? "•••••••• (unchanged)" : "xapp-..."}
                    />
                  )}
                </Field>
              </div>

              {!isNew && (
                <div className="flex items-center gap-3 pt-2">
                  <button
                    type="button"
                    onClick={handleTestConnection}
                    disabled={testing}
                    data-testid="test-connection-button"
                    className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-border bg-surface-elevated text-xs text-foreground disabled:opacity-50"
                  >
                    <RefreshCw className={`w-3.5 h-3.5 ${testing ? "animate-spin" : ""}`} />
                    <span>Test connection (auth.test)</span>
                  </button>
                  {testResult && (
                    <p className={`text-xs ${testResult.ok ? "text-emerald-400" : "text-rose-400"}`}>
                      {testResult.message}
                    </p>
                  )}
                </div>
              )}

              {/* Manifest Generator */}
              {!isNew && manifestData && (
                <div className="pt-4 border-t border-border/60 space-y-2">
                  <div className="flex items-center justify-between">
                    <div>
                      <span className="text-xs font-semibold text-foreground">Slack App Manifest</span>
                      <p className="text-[10px] text-muted-foreground">
                        Paste this manifest into api.slack.com to configure scopes and Socket Mode events.
                      </p>
                    </div>
                    <div className="flex items-center gap-2">
                      <button
                        type="button"
                        onClick={() => navigator.clipboard.writeText(manifestData)}
                        className="flex items-center gap-1 px-2.5 py-1 rounded bg-surface-elevated border border-border text-[11px] text-foreground hover:bg-surface-subnav"
                      >
                        <Copy className="w-3 h-3" />
                        <span>Copy</span>
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          const blob = new Blob([manifestData], { type: "application/json" });
                          const url = URL.createObjectURL(blob);
                          const a = document.createElement("a");
                          a.href = url;
                          a.download = `${name.toLowerCase().replace(/\s+/g, "-")}-manifest.json`;
                          a.click();
                        }}
                        className="flex items-center gap-1 px-2.5 py-1 rounded bg-surface-elevated border border-border text-[11px] text-foreground hover:bg-surface-subnav"
                      >
                        <Download className="w-3 h-3" />
                        <span>Download</span>
                      </button>
                    </div>
                  </div>
                  <pre className="p-3 rounded-lg bg-surface-subnav border border-surface-border text-[10px] text-neutral-300 font-mono max-h-48 overflow-y-auto">
                    {loadingManifest ? "Generating manifest..." : manifestData}
                  </pre>
                </div>
              )}
            </div>
          )}

          {/* TAB: Behaviour */}
          {activeTab === "behaviour" && (
            <div className="space-y-5">
              <Field label="Default Response Mode" hint="Choose how this bot answers when invoked in a channel.">
                {(props) => (
                  <Select
                    {...props}
                    data-testid="bot-response-mode-select"
                    value={responseMode}
                    onChange={(e) => setResponseMode(e.target.value as "thread" | "private")}
                  >
                    <option value="thread">Thread (publicly in the Slack thread)</option>
                    <option value="private">Private session (ephemeral link &amp; private summary)</option>
                  </Select>
                )}
              </Field>

              <div className="space-y-3 p-3.5 rounded-lg border border-border bg-surface-subnav/30">
                <span className="text-xs font-semibold text-foreground">Triggers &amp; Channels</span>
                <label className="flex items-center gap-2.5 text-xs text-foreground cursor-pointer">
                  <input
                    type="checkbox"
                    checked={respondInDms}
                    onChange={(e) => setRespondInDms(e.target.checked)}
                    className="rounded border-border text-brand focus:ring-brand"
                  />
                  <span>Respond to direct messages (DMs) with this bot</span>
                </label>

                <Field
                  label="Listen without mention in channels (optional)"
                  hint="Comma-separated channel IDs (e.g. C01234ABC, C05678DEF) where this bot routes every message."
                >
                  {(props) => (
                    <Input
                      {...props}
                      value={listenChannels}
                      onChange={(e) => setListenChannels(e.target.value)}
                      placeholder="C01234ABC, C05678DEF"
                    />
                  )}
                </Field>
              </div>

              {responseMode === "private" && (
                <div className="space-y-3 p-3.5 rounded-lg border border-border bg-surface-subnav/30">
                  <span className="text-xs font-semibold text-foreground">Private Mode Settings</span>
                  <Field label="Acknowledgement Reaction" hint="Slack emoji name added to user's message when taken.">
                    {(props) => (
                      <Input
                        {...props}
                        value={privateReaction}
                        onChange={(e) => setPrivateReaction(e.target.value)}
                        placeholder="white_check_mark"
                      />
                    )}
                  </Field>
                  <label className="flex items-center gap-2.5 text-xs text-foreground cursor-pointer">
                    <input
                      type="checkbox"
                      checked={privateDmResult}
                      onChange={(e) => setPrivateDmResult(e.target.checked)}
                      className="rounded border-border text-brand focus:ring-brand"
                    />
                    <span>Also send completion summary as a direct message (DM)</span>
                  </label>
                  <label className="flex items-center gap-2.5 text-xs text-foreground cursor-pointer">
                    <input
                      type="checkbox"
                      data-testid="private-dm-updates"
                      checked={privateDmUpdates}
                      onChange={(e) => setPrivateDmUpdates(e.target.checked)}
                      className="rounded border-border text-brand focus:ring-brand"
                    />
                    <span>Send questions, approvals, errors and the result by direct message, with a link to the thread</span>
                  </label>
                </div>
              )}
            </div>
          )}

          {/* TAB: Brain */}
          {activeTab === "brain" && (
            <div className="space-y-5">
              <div className="flex gap-4">
                <button
                  type="button"
                  onClick={() => setBrainType("agent")}
                  className={`flex-1 p-4 rounded-xl border text-left transition-all ${
                    brainType === "agent"
                      ? "border-brand bg-brand/5 ring-2 ring-brand/20"
                      : "border-border bg-surface-card hover:border-neutral-400"
                  }`}
                >
                  <div className="flex items-center gap-2">
                    <Cpu className="w-4 h-4 text-brand" />
                    <span className="text-sm font-semibold text-foreground">One Agent</span>
                  </div>
                  <p className="text-xs text-muted-foreground mt-1">
                    Every message to this bot is answered by a single selected agent persona.
                  </p>
                </button>

                <button
                  type="button"
                  onClick={() => setBrainType("router")}
                  className={`flex-1 p-4 rounded-xl border text-left transition-all ${
                    brainType === "router"
                      ? "border-brand bg-brand/5 ring-2 ring-brand/20"
                      : "border-border bg-surface-card hover:border-neutral-400"
                  }`}
                >
                  <div className="flex items-center gap-2">
                    <Layers className="w-4 h-4 text-brand" />
                    <span className="text-sm font-semibold text-foreground">Router</span>
                  </div>
                  <p className="text-xs text-muted-foreground mt-1">
                    An AI classifier evaluates messages and routes them to specialized agents.
                  </p>
                </button>
              </div>

              {brainType === "agent" && (
                <Field label="Target Agent Persona" hint="The persona that executes turns for this bot.">
                  {(props) => (
                    <Select
                      {...props}
                      data-testid="bot-agent-select"
                      value={agentId}
                      onChange={(e) => setAgentId(e.target.value)}
                    >
                      {agentOptions.map((opt) => (
                        <option key={opt.value} value={opt.value}>
                          {opt.label}
                        </option>
                      ))}
                    </Select>
                  )}
                </Field>
              )}
            </div>
          )}

          {/* TAB: Router */}
          {activeTab === "router" && brainType === "router" && (
            <div className="space-y-6">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <Field label="Classifier Agent Persona" hint="Persona providing routing defaults and context.">
                  {(props) => (
                    <Select
                      {...props}
                      data-testid="classifier-agent-select"
                      value={classifierAgentId}
                      onChange={(e) => setClassifierAgentId(e.target.value)}
                    >
                      {agentOptions.map((opt) => (
                        <option key={opt.value} value={opt.value}>
                          {opt.label}
                        </option>
                      ))}
                    </Select>
                  )}
                </Field>

                <Field
                  label="Classifier model"
                  hint="Empty uses the classifier agent's model. OpenRouter System One models return a probability per route."
                >
                  {(props) => (
                    <div className="space-y-1.5">
                      <ModelPicker
                        {...props}
                        data-testid="classifier-model"
                        value={classifierModel}
                        onChange={setClassifierModel}
                        inheritLabel="Classifier agent's model"
                      />
                      <div className="flex flex-wrap gap-1.5 pt-0.5">
                        <button
                          type="button"
                          onClick={() => setClassifierModel("openrouter/typesafe/jev-latest")}
                          className="text-[10px] px-2 py-0.5 rounded bg-brand/10 text-brand border border-brand/20 hover:bg-brand/20 font-medium"
                        >
                          ⚡ Jev Latest (System One)
                        </button>
                        <button
                          type="button"
                          onClick={() => setClassifierModel("openrouter/inception/mercury-decide")}
                          className="text-[10px] px-2 py-0.5 rounded bg-surface-subnav text-foreground border border-border hover:bg-surface-elevated"
                        >
                          Mercury Decide
                        </button>
                      </div>
                    </div>
                  )}
                </Field>
              </div>

              {/* Routes List */}
              <div className="space-y-3">
                <div className="flex items-center justify-between">
                  <div>
                    <span className="text-xs font-semibold text-foreground">Routing Destinations</span>
                    <p className="text-[10px] text-muted-foreground">
                      Each route provides classification context and points to an agent persona.
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={addRoute}
                    className="flex items-center gap-1 px-2.5 py-1 rounded-lg bg-brand text-white text-xs font-medium hover:bg-brand/90"
                  >
                    <Plus className="w-3.5 h-3.5" />
                    <span>Add Route</span>
                  </button>
                </div>

                <div className="space-y-3">
                  {routes.map((route, idx) => (
                    <div
                      key={idx}
                      className="p-3 rounded-lg border border-border bg-surface-subnav/30 space-y-2.5"
                    >
                      <div className="flex items-center justify-between gap-3">
                        <Input
                          placeholder="Route name (e.g. dev_bug)"
                          value={route.name}
                          onChange={(e) => updateRoute(idx, { name: e.target.value })}
                          className="font-medium text-xs h-8 flex-1"
                        />
                        <Select
                          value={route.agent_id}
                          onChange={(e) => updateRoute(idx, { agent_id: e.target.value })}
                          className="text-xs h-8 flex-1"
                        >
                          {agentOptions.map((opt) => (
                            <option key={opt.value} value={opt.value}>
                              {opt.label}
                            </option>
                          ))}
                        </Select>
                        <Select
                          value={route.response_mode}
                          onChange={(e) => updateRoute(idx, { response_mode: e.target.value })}
                          className="text-xs h-8 w-32"
                        >
                          <option value="">(Inherit mode)</option>
                          <option value="thread">Thread</option>
                          <option value="private">Private</option>
                        </Select>
                        <button
                          type="button"
                          onClick={() => removeRoute(idx)}
                          className="p-1.5 text-muted-foreground hover:text-rose-400 rounded"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      </div>

                      <Textarea
                        rows={2}
                        placeholder="Description: when should this route be selected? (e.g. User wants to fix code bugs)"
                        value={route.description}
                        onChange={(e) => updateRoute(idx, { description: e.target.value })}
                        className="text-xs leading-normal"
                      />
                    </div>
                  ))}
                </div>
              </div>

              {/* Dismiss & Fallback Settings */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4 p-3 rounded-lg border border-border bg-surface-subnav/20">
                <div className="space-y-3">
                  <label className="flex items-center gap-2 text-xs font-semibold text-foreground cursor-pointer">
                    <input
                      type="checkbox"
                      checked={allowDismiss}
                      onChange={(e) => setAllowDismiss(e.target.checked)}
                      className="rounded border-border text-brand focus:ring-brand"
                    />
                    <span>Allow Dismissal (no session created)</span>
                  </label>
                  {allowDismiss && (
                    <Field label="Dismissal criteria">
                      {(props) => (
                        <Input
                          {...props}
                          value={dismissDescription}
                          onChange={(e) => setDismissDescription(e.target.value)}
                          placeholder="Small talk, casual greetings, or out-of-scope messages"
                        />
                      )}
                    </Field>
                  )}
                  <label className="flex items-center gap-2 text-xs font-semibold text-foreground cursor-pointer pt-1">
                    <input
                      type="checkbox"
                      checked={sticky}
                      onChange={(e) => setSticky(e.target.checked)}
                      className="rounded border-border text-brand focus:ring-brand"
                    />
                    <span>Sticky routing (thread follow-ups keep previous route)</span>
                  </label>
                </div>

                <div className="space-y-3">
                  <Field label="Minimum Confidence (0.0 to 1.0)">
                    {(props) => (
                      <Input
                        {...props}
                        type="number"
                        step="0.05"
                        min="0"
                        max="1"
                        value={minConfidence}
                        onChange={(e) => setMinConfidence(parseFloat(e.target.value))}
                      />
                    )}
                  </Field>

                  <Field label="Fallback Route" hint="Used when confidence is below minimum.">
                    {(props) => (
                      <Select {...props} value={fallback} onChange={(e) => setFallback(e.target.value)}>
                        <option value="">(None - Bot default)</option>
                        {routes.map((r) => (
                          <option key={r.name} value={r.name}>
                            Route: {r.name}
                          </option>
                        ))}
                        {allowDismiss && <option value="dismiss">Dismiss</option>}
                      </Select>
                    )}
                  </Field>
                </div>
              </div>

              {/* Try It Box */}
              {!isNew && (
                <div className="p-4 rounded-lg border border-border bg-surface-card space-y-3" data-testid="router-try-box">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-semibold text-foreground flex items-center gap-1.5">
                      <Sparkles className="w-3.5 h-3.5 text-brand" />
                      Try Router Classification
                    </span>
                    <button
                      type="button"
                      onClick={handleTryRouter}
                      disabled={trying || !tryText.trim()}
                      data-testid="router-try-submit"
                      className="flex items-center gap-1 px-3 py-1.5 rounded-lg bg-brand text-white text-xs font-semibold disabled:opacity-50"
                    >
                      <Play className="w-3 h-3" />
                      <span>{trying ? "Classifying..." : "Test route"}</span>
                    </button>
                  </div>

                  <Textarea
                    rows={2}
                    data-testid="router-try-input"
                    placeholder="Enter sample message to test classification..."
                    value={tryText}
                    onChange={(e) => setTryText(e.target.value)}
                    className="text-xs"
                  />
                  <Input
                    placeholder="Optional thread context excerpt..."
                    value={tryExcerpt}
                    onChange={(e) => setTryExcerpt(e.target.value)}
                    className="text-xs h-8"
                  />

                  {tryResult && (
                    <div className="p-3 rounded-lg bg-surface-subnav border border-border text-xs space-y-2">
                      <div className="flex items-center justify-between">
                        <span className="font-semibold text-foreground">
                          Chosen route: <code className="text-brand">{String(tryResult.route)}</code> (applied:{" "}
                          <code>{String(tryResult.applied)}</code>)
                        </span>
                        <span className="text-muted-foreground text-[10px]">
                          Confidence: {Math.round(Number(tryResult.confidence) * 100)}% · {String(tryResult.latency_ms)}ms
                        </span>
                      </div>
                      {Boolean(tryResult.probabilities && Object.keys(tryResult.probabilities as Record<string, unknown>).length > 0) && (
                        <div className="space-y-1 pt-1.5 border-t border-border/40">
                          <span className="text-[10px] font-medium text-muted-foreground uppercase tracking-wider">
                            Calibrated Probabilities (System One)
                          </span>
                          <div className="flex flex-wrap gap-1.5">
                            {Object.entries(tryResult.probabilities as Record<string, number>).map(([name, prob]) => (
                              <div
                                key={name}
                                className="flex items-center gap-1 text-[11px] bg-surface-card px-2 py-0.5 rounded border border-border/60"
                              >
                                <span className="font-mono text-muted-foreground">{name}:</span>
                                <span className={`font-semibold ${name === tryResult.route ? "text-brand" : "text-foreground"}`}>
                                  {Math.round(prob * 100)}%
                                </span>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                      <p className="text-neutral-400 text-[11px]">{String(tryResult.reason)}</p>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          {/* TAB: Channels */}
          {activeTab === "channels" && (
            <div className="space-y-4">
              <ChannelProfilesEditor
                value={(policyOverrides.channel_profiles as Record<string, ChannelProfile> | undefined) ?? {}}
                agentOptions={agentOptions}
                onChange={(next) => {
                  const rest = { ...policyOverrides };
                  delete rest.channel_profiles;
                  setPolicyOverrides(Object.keys(next).length > 0 ? { ...rest, channel_profiles: next } : rest);
                }}
              />
            </div>
          )}

          {/* TAB: Policy */}
          {activeTab === "policy" && (
            <div className="space-y-4">
              <div className="flex items-center justify-between pb-2 border-b border-border/60">
                <span className="text-xs font-semibold text-foreground">Bot Policy Overrides</span>
                <InheritanceBadge
                  status={Object.keys(policyOverrides).length > 0 ? "overridden" : "org_default"}
                  onReset={() => setPolicyOverrides({})}
                />
              </div>

              <Field label="DM Policy" hint="Who may message this bot directly.">
                {(props) => (
                  <Select
                    {...props}
                    value={String(policyOverrides.dm_policy || "")}
                    onChange={(e) => setPolicyOverrides({ ...policyOverrides, dm_policy: e.target.value })}
                  >
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
                    value={String(policyOverrides.outbound_channels || "")}
                    onChange={(e) => setPolicyOverrides({ ...policyOverrides, outbound_channels: e.target.value })}
                    placeholder="Leave empty to inherit organization default"
                  />
                )}
              </Field>
            </div>
          )}

          {/* TAB: Activity */}
          {activeTab === "activity" && (
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold text-foreground">Recent Routing Decisions</span>
                <button
                  type="button"
                  onClick={loadDecisions}
                  disabled={loadingDecisions}
                  className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
                >
                  <RefreshCw className={`w-3 h-3 ${loadingDecisions ? "animate-spin" : ""}`} />
                  <span>Refresh</span>
                </button>
              </div>

              {decisions.length === 0 ? (
                <p className="text-xs text-muted-foreground p-4 text-center">No decisions recorded yet.</p>
              ) : (
                <div className="border border-border rounded-lg overflow-hidden">
                  <table className="w-full text-left text-xs">
                    <thead className="bg-surface-subnav/80 border-b border-border text-muted-foreground">
                      <tr>
                        <th className="p-2.5">Time</th>
                        <th className="p-2.5">Route</th>
                        <th className="p-2.5">Confidence</th>
                        <th className="p-2.5">Excerpt</th>
                        <th className="p-2.5">Latency</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border/60">
                      {decisions.map((dec) => (
                        <tr key={dec.id} className="hover:bg-surface-elevated/40">
                          <td className="p-2.5 text-muted-foreground whitespace-nowrap">
                            {new Date(dec.created_at * 1000).toLocaleTimeString()}
                          </td>
                          <td className="p-2.5 font-medium text-brand">{dec.applied || dec.route}</td>
                          <td className="p-2.5">{Math.round(dec.confidence * 100)}%</td>
                          <td className="p-2.5 max-w-xs truncate" title={dec.excerpt}>
                            {dec.excerpt || "—"}
                          </td>
                          <td className="p-2.5 text-muted-foreground">{dec.latency_ms}ms</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-3 px-6 py-3 border-t border-border/70 bg-surface-subnav/50">
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-2 rounded-lg border border-border text-xs text-foreground hover:bg-surface-elevated"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleSave}
            disabled={saving}
            data-testid="save-bot-button"
            className="flex items-center gap-1.5 px-4 py-2 rounded-lg bg-brand text-white text-xs font-semibold hover:bg-brand/90 disabled:opacity-50"
          >
            <Save className="w-3.5 h-3.5" />
            <span>{saving ? "Saving..." : isNew ? "Create bot" : "Save changes"}</span>
          </button>
        </div>
      </div>
    </div>
  );
}
