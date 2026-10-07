"use client";

import React, { useState, useEffect } from "react";
import { AtSign, Save, CheckCircle2, AlertCircle } from "lucide-react";

export default function UserSlackSettingsPage() {
  const [slackPrefs, setSlackPrefs] = useState({
    slack_user_handle: "@alex",
    slack_notifications_enabled: true,
  });
  const [saving, setSaving] = useState(false);
  const [successBanner, setSuccessBanner] = useState<string | null>(null);
  const [errorBanner, setErrorBanner] = useState<string | null>(null);

  useEffect(() => {
    fetch("/v1/settings/user_preferences")
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (data?.effective) {
          setSlackPrefs((prev) => ({ ...prev, ...data.effective }));
        }
      })
      .catch(() => {});
  }, []);

  const handleSave = async () => {
    setSaving(true);
    setSuccessBanner(null);
    setErrorBanner(null);
    try {
      const res = await fetch("/v1/settings/user_preferences", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ overrides: slackPrefs }),
      });
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        setErrorBanner(errData.detail || `Failed to save preferences (${res.status})`);
        return;
      }
      setSuccessBanner("Slack routing preferences saved");
      setTimeout(() => setSuccessBanner(null), 3000);
    } catch {
      setErrorBanner("Network error: failed to connect to server");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="flex flex-col min-h-full">
      <div className="border-b border-surface-border bg-surface-subnav/50 px-8 py-6">
        <div className="max-w-5xl mx-auto">
          <div className="flex items-center gap-2 text-xs font-mono text-neutral-500 dark:text-neutral-400 mb-2">
            <span>Settings</span>
            <span>/</span>
            <span>User Preferences</span>
            <span>/</span>
            <span className="text-foreground font-medium">Slack Routing & Mentions</span>
          </div>
          <h1 className="text-xl md:text-2xl font-display font-bold text-foreground tracking-tight">
            Slack Identity & Mention Routing
          </h1>
          <p className="text-xs text-neutral-500 dark:text-neutral-400 mt-1.5 font-sans">
            Connect your personal workspace Slack handle for interactive mentions, approval pings, and thread handoffs.
          </p>
        </div>
      </div>

      <div className="flex-1 p-8">
        <div className="max-w-5xl mx-auto space-y-6">
          {successBanner && (
            <div className="flex items-center gap-2 p-3.5 rounded-xl bg-emerald-950/60 border border-emerald-800/60 text-emerald-400 text-xs font-mono">
              <CheckCircle2 className="w-4 h-4 shrink-0" />
              <span>{successBanner}</span>
            </div>
          )}

          {errorBanner && (
            <div className="flex items-center gap-2 p-3.5 rounded-xl bg-rose-950/60 border border-rose-800/60 text-rose-400 text-xs font-mono">
              <AlertCircle className="w-4 h-4 shrink-0" />
              <span>{errorBanner}</span>
            </div>
          )}

          <div className="bg-surface-card rounded-xl border border-surface-border p-6 space-y-5">
            <div>
              <label className="block text-xs font-medium text-neutral-700 dark:text-neutral-300 mb-1.5">
                Personal Slack Member Handle
              </label>
              <div className="flex items-center max-w-sm rounded-lg bg-surface-subnav border border-surface-border focus-within:border-brand px-3 py-2">
                <AtSign className="w-3.5 h-3.5 text-neutral-400 mr-1" />
                <input
                  type="text"
                  value={slackPrefs.slack_user_handle}
                  onChange={(e) =>
                    setSlackPrefs({ ...slackPrefs, slack_user_handle: e.target.value })
                  }
                  className="bg-transparent text-xs font-mono text-foreground focus:outline-none flex-1"
                />
              </div>
            </div>

            <div className="flex items-center justify-between pt-4 border-t border-surface-borderSubtle">
              <div className="space-y-1">
                <span className="text-sm font-medium text-foreground">Direct Approval Notifications</span>
                <p className="text-xs text-neutral-500 dark:text-neutral-400">
                  Receive Slack DMs when high-risk actions require interactive passkey verification.
                </p>
              </div>
              <label className="relative inline-flex items-center cursor-pointer flex-shrink-0">
                <input
                  type="checkbox"
                  checked={slackPrefs.slack_notifications_enabled}
                  onChange={(e) =>
                    setSlackPrefs({
                      ...slackPrefs,
                      slack_notifications_enabled: e.target.checked,
                    })
                  }
                  className="sr-only peer"
                />
                <div className="w-11 h-6 bg-surface-highlight peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-brand" />
              </label>
            </div>
          </div>
        </div>
      </div>

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
