"use client";

import React, { useState, useEffect } from "react";
import {
  Link2,
  CheckCircle2,
  AlertCircle,
  Save,
  Sparkles,
  RefreshCw,
  ExternalLink,
} from "lucide-react";

interface SlackSettingsData {
  default_agent_persona: string;
  enable_interactive_session_switcher: boolean;
  auto_link_by_verified_email: boolean;
  allowed_tools: string[];
}

export default function SlackSettingsPage() {
  const [saving, setSaving] = useState(false);
  const [successBanner, setSuccessBanner] = useState<string | null>(null);
  const [errorBanner, setErrorBanner] = useState<string | null>(null);

  const [settings, setSettings] = useState<SlackSettingsData>({
    default_agent_persona: "code_architect",
    enable_interactive_session_switcher: true,
    auto_link_by_verified_email: true,
    allowed_tools: [
      "web_fetch",
      "web_search",
      "read_file",
      "session_rename",
      "update_task_checklist",
    ],
  });

  const [thoughtDisplayMode, setThoughtDisplayMode] = useState<"invisible" | "discreet">(
    "invisible"
  );

  const fetchSettings = async () => {
    try {
      const res = await fetch("/v1/settings/slack");
      if (res.ok) {
        const data = await res.json();
        if (data.effective) {
          setSettings((prev) => ({ ...prev, ...data.effective }));
        }
      }
    } catch (err) {
      console.error("Failed to load Slack settings:", err);
    }
  };

  useEffect(() => {
    fetchSettings();
  }, []);

  const handleSave = async () => {
    setSaving(true);
    setErrorBanner(null);
    setSuccessBanner(null);
    try {
      const res = await fetch("/v1/settings/slack", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ overrides: settings }),
      });
      if (!res.ok) {
        let errMessage = "";
        try {
          const data = await res.json();
          errMessage = data.detail || data.message || JSON.stringify(data);
        } catch {
          errMessage = await res.text();
        }
        throw new Error(errMessage || `Failed to update Slack settings (HTTP ${res.status})`);
      }
      setSuccessBanner("Slack Assistant settings and thread presentation policies saved");
      setTimeout(() => setSuccessBanner(null), 3000);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to update Slack settings";
      setErrorBanner(msg);
      setTimeout(() => setErrorBanner(null), 6000);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="flex flex-col min-h-full">
      {/* Scope Summary Banner matching Stitch */}
      <div className="border-b border-surface-border bg-surface-subnav/50 px-8 py-6">
        <div className="max-w-5xl mx-auto flex flex-col md:flex-row md:items-center justify-between gap-4">
          <div>
            <div className="flex items-center gap-2 text-xs font-mono text-neutral-500 dark:text-neutral-400 mb-2">
              <span>Settings</span>
              <span>/</span>
              <span>Organization</span>
              <span>/</span>
              <span className="text-foreground font-medium">Slack & Chat Ops</span>
            </div>
            <div className="flex flex-wrap items-center gap-3">
              <h1 className="text-xl md:text-2xl font-display font-bold text-foreground tracking-tight">
                Slack Assistant & Chat Ops
              </h1>
              <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-mono bg-emerald-950/80 text-emerald-400 border border-emerald-800/50">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
                Socket Mode Active · 2 Channels Linked
              </span>
            </div>
            <p className="text-xs text-neutral-500 dark:text-neutral-400 mt-1.5 font-sans">
              Manage Slack main thread message rendering, invisible thought synthesis, and automated approval gates.
            </p>
          </div>

          <div className="flex items-center gap-2 self-start md:self-center">
            <button
              type="button"
              onClick={fetchSettings}
              className="px-3.5 py-2 rounded-lg bg-surface-card hover:bg-surface-elevated text-xs font-mono text-foreground border border-surface-border flex items-center gap-2 transition-colors cursor-pointer"
            >
              <RefreshCw className="w-4 h-4 text-neutral-400" />
              <span>Test Socket Mode</span>
            </button>
            <a
              href="https://api.slack.com/apps"
              target="_blank"
              rel="noopener noreferrer"
              className="p-2 rounded-lg bg-surface-card hover:bg-surface-elevated text-neutral-500 hover:text-foreground dark:text-neutral-400 dark:hover:text-white border border-surface-border transition-colors"
              title="Slack App Console"
            >
              <ExternalLink className="w-4 h-4" />
            </a>
          </div>
        </div>
      </div>

      <div className="flex-1 p-8">
        <div className="max-w-5xl mx-auto space-y-8">
          {errorBanner && (
            <div className="flex items-center gap-2 p-3.5 rounded-xl bg-rose-950/60 border border-rose-800/60 text-rose-400 text-xs font-mono">
              <AlertCircle className="w-4 h-4 shrink-0" />
              <span>{errorBanner}</span>
            </div>
          )}

          {successBanner && (
            <div className="flex items-center gap-2 p-3.5 rounded-xl bg-emerald-950/60 border border-emerald-800/60 text-emerald-400 text-xs font-mono">
              <CheckCircle2 className="w-4 h-4 shrink-0" />
              <span>{successBanner}</span>
            </div>
          )}

          {/* Stitch Thread Presentation Style */}
          <section className="space-y-4">
            <div className="flex items-center justify-between pb-2 border-b border-surface-border">
              <div className="flex items-center gap-2.5">
                <div className="w-6 h-6 rounded bg-brand/10 border border-brand/20 flex items-center justify-center text-brand">
                  <Sparkles className="w-3.5 h-3.5" />
                </div>
                <h2 className="text-base font-display font-semibold text-foreground">
                  Main Thread Presentation & Thought Process
                </h2>
              </div>
              <span className="text-[11px] font-mono text-brand bg-brand/10 px-2 py-0.5 rounded border border-brand/20">
                STITCH EDITORIAL
              </span>
            </div>

            <div className="bg-surface-card rounded-xl border border-surface-border p-5 space-y-4">
              <div>
                <label className="text-sm font-medium text-foreground block">
                  Slack Thread Thought & Reasoning Visibility
                </label>
                <p className="text-xs text-neutral-500 dark:text-neutral-400 mt-0.5">
                  The Stitch design standard calls for clean main thread responses with minimal friction. Thought processes are kept invisible in main chat responses, surfaced only via ephemeral spinner status or discreet sub-thread accordions.
                </p>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {/* Option 1: Clean & Invisible Thought (Recommended) */}
                <label
                  onClick={() => setThoughtDisplayMode("invisible")}
                  className={`relative flex items-start gap-3.5 p-4 rounded-lg cursor-pointer transition-all ${
                    thoughtDisplayMode === "invisible"
                      ? "border-2 border-brand bg-surface-elevated/70"
                      : "border border-surface-border hover:border-neutral-400 dark:hover:border-neutral-600 bg-surface-subnav"
                  }`}
                >
                  <input
                    type="radio"
                    name="thought_display"
                    value="invisible"
                    checked={thoughtDisplayMode === "invisible"}
                    onChange={() => setThoughtDisplayMode("invisible")}
                    className="mt-1 text-brand focus:ring-brand focus:ring-offset-surface-base bg-surface-card border-neutral-600"
                  />
                  <div className="space-y-1">
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-mono font-semibold text-foreground">
                        Invisible Thought (Pure Synthesis)
                      </span>
                      <span className="text-[9px] font-mono px-1.5 py-0.5 uppercase rounded bg-brand/20 text-brand">
                        Stitch Standard
                      </span>
                    </div>
                    <p className="text-xs text-neutral-500 dark:text-neutral-400 leading-relaxed">
                      Reasoning occurs silently in the background with ephemeral status updates (e.g. <code>Running test suite...</code>). Only the final verified response and clean Block Kit diffs appear in the thread.
                    </p>
                  </div>
                </label>

                {/* Option 2: Discreet Collapsed Footer */}
                <label
                  onClick={() => setThoughtDisplayMode("discreet")}
                  className={`relative flex items-start gap-3.5 p-4 rounded-lg cursor-pointer transition-all ${
                    thoughtDisplayMode === "discreet"
                      ? "border-2 border-brand bg-surface-elevated/70"
                      : "border border-surface-border hover:border-neutral-400 dark:hover:border-neutral-600 bg-surface-subnav"
                  }`}
                >
                  <input
                    type="radio"
                    name="thought_display"
                    value="discreet"
                    checked={thoughtDisplayMode === "discreet"}
                    onChange={() => setThoughtDisplayMode("discreet")}
                    className="mt-1 text-brand focus:ring-brand focus:ring-offset-surface-base bg-surface-card border-neutral-600"
                  />
                  <div className="space-y-1">
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-mono font-semibold text-foreground">
                        Discreet Context Pill
                      </span>
                    </div>
                    <p className="text-xs text-neutral-500 dark:text-neutral-400 leading-relaxed">
                      Includes a minimal <code>Thought for 1.4s · 3 tools executed</code> context indicator in the footer without cluttering the chat flow.
                    </p>
                  </div>
                </label>
              </div>
            </div>
          </section>

          {/* Account Linking & Session Controls */}
          <section className="space-y-4">
            <div className="flex items-center justify-between pb-2 border-b border-surface-border">
              <div className="flex items-center gap-2.5">
                <div className="w-6 h-6 rounded bg-neutral-200 dark:bg-neutral-800 border border-surface-border flex items-center justify-center text-foreground dark:text-neutral-300">
                  <Link2 className="w-3.5 h-3.5" />
                </div>
                <h2 className="text-base font-display font-semibold text-foreground">
                  Slack Account & Session Federation
                </h2>
              </div>
            </div>

            <div className="bg-surface-card rounded-xl border border-surface-border p-5 space-y-4">
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
                <div className="space-y-1">
                  <span className="text-sm font-medium text-foreground">
                    Auto-Link by Verified Corporate Email
                  </span>
                  <p className="text-xs text-neutral-500 dark:text-neutral-400 leading-relaxed">
                    Automatically federate incoming Slack requests with developer identities matching the corporate email domain.
                  </p>
                </div>
                <label className="relative inline-flex items-center cursor-pointer flex-shrink-0">
                  <input
                    type="checkbox"
                    checked={settings.auto_link_by_verified_email}
                    onChange={(e) =>
                      setSettings({
                        ...settings,
                        auto_link_by_verified_email: e.target.checked,
                      })
                    }
                    className="sr-only peer"
                  />
                  <div className="w-11 h-6 bg-surface-highlight peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-brand" />
                </label>
              </div>

              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pt-4 border-t border-surface-borderSubtle">
                <div className="space-y-1">
                  <span className="text-sm font-medium text-foreground">
                    Interactive Thread Session Switcher
                  </span>
                  <p className="text-xs text-neutral-500 dark:text-neutral-400 leading-relaxed">
                    Allows developers to switch active sandbox branches and agent personas directly inside Slack threads.
                  </p>
                </div>
                <label className="relative inline-flex items-center cursor-pointer flex-shrink-0">
                  <input
                    type="checkbox"
                    checked={settings.enable_interactive_session_switcher}
                    onChange={(e) =>
                      setSettings({
                        ...settings,
                        enable_interactive_session_switcher: e.target.checked,
                      })
                    }
                    className="sr-only peer"
                  />
                  <div className="w-11 h-6 bg-surface-highlight peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-brand" />
                </label>
              </div>
            </div>
          </section>
        </div>
      </div>

      {/* Sticky Bottom Save */}
      <div className="sticky bottom-0 z-20 bg-surface-sidebar/95 backdrop-blur-md border-t border-surface-border px-8 py-3.5 flex items-center justify-end">
        <button
          type="button"
          onClick={handleSave}
          disabled={saving}
          className="px-5 py-2 rounded-lg bg-brand hover:bg-brand-hover text-white text-xs font-display font-semibold flex items-center gap-2 cursor-pointer disabled:opacity-50"
        >
          <Save className="w-4 h-4" />
          <span>{saving ? "Saving..." : "Save Changes"}</span>
        </button>
      </div>
    </div>
  );
}
