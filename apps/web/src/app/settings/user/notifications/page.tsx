"use client";

import React, { useState, useEffect } from "react";
import { Save, CheckCircle2 } from "lucide-react";

export default function UserNotificationsSettingsPage() {
  const [notifPrefs, setNotifPrefs] = useState({
    notification_sound: true,
    theme_mode: "dark",
  });
  const [saving, setSaving] = useState(false);
  const [successBanner, setSuccessBanner] = useState<string | null>(null);

  useEffect(() => {
    fetch("/v1/settings/user_preferences")
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (data?.effective) {
          setNotifPrefs((prev) => ({ ...prev, ...data.effective }));
        }
      })
      .catch(() => {});
  }, []);

  const handleSave = async () => {
    setSaving(true);
    try {
      await fetch("/v1/settings/user_preferences", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ overrides: notifPrefs }),
      });
      setSuccessBanner("Notification preferences saved");
      setTimeout(() => setSuccessBanner(null), 3000);
    } catch {
      // ignore
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
            <span className="text-foreground font-medium">Notification Channels</span>
          </div>
          <h1 className="text-xl md:text-2xl font-display font-bold text-foreground tracking-tight">
            Notification Channels & Audio Alerts
          </h1>
          <p className="text-xs text-neutral-500 dark:text-neutral-400 mt-1.5 font-sans">
            Manage audio chime triggers and cockpit telemetry alert sounds.
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

          <div className="bg-surface-card rounded-xl border border-surface-border p-6 space-y-5">
            <div className="flex items-center justify-between">
              <div className="space-y-1">
                <span className="text-sm font-medium text-foreground">Cockpit Chime & Sound Effects</span>
                <p className="text-xs text-neutral-500 dark:text-neutral-400">
                  Play subtle telemetry chimes when reasoning phases complete and user clarification is requested.
                </p>
              </div>
              <label className="relative inline-flex items-center cursor-pointer flex-shrink-0">
                <input
                  type="checkbox"
                  checked={notifPrefs.notification_sound}
                  onChange={(e) =>
                    setNotifPrefs({
                      ...notifPrefs,
                      notification_sound: e.target.checked,
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
