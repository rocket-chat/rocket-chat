"use client";

import React, { useState, useEffect } from "react";
import { Save, CheckCircle2, AlertCircle } from "lucide-react";

import { useSession } from "next-auth/react";

interface ProfileState {
  full_name: string;
  default_role: string;
  git_author_email: string;
  suggest_next_questions?: boolean;
}

export default function UserProfileSettingsPage() {
  const { data: session } = useSession();
  const [profile, setProfile] = useState<ProfileState>({
    full_name: "",
    default_role: "Software Engineer",
    git_author_email: "",
    suggest_next_questions: true,
  });
  const [saving, setSaving] = useState(false);
  const [successBanner, setSuccessBanner] = useState<string | null>(null);
  const [errorBanner, setErrorBanner] = useState<string | null>(null);

  useEffect(() => {
    fetch("/v1/settings/user_preferences")
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (data?.effective) {
          setProfile((prev) => ({
            ...prev,
            ...data.effective,
            full_name: data.effective.full_name || session?.user?.name || prev.full_name,
            git_author_email: data.effective.git_author_email || session?.user?.email || prev.git_author_email,
          }));
        } else if (session?.user) {
          setProfile((prev) => ({
            ...prev,
            full_name: session.user?.name || prev.full_name,
            git_author_email: session.user?.email || prev.git_author_email,
          }));
        }
      })
      .catch(() => {});
  }, [session]);

  const handleSave = async () => {
    setSaving(true);
    setSuccessBanner(null);
    setErrorBanner(null);
    try {
      const res = await fetch("/v1/settings/user_preferences", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ overrides: profile }),
      });
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        setErrorBanner(errData.detail || `Failed to save profile (${res.status})`);
        return;
      }
      setSuccessBanner("Profile preferences saved successfully");
      setTimeout(() => setSuccessBanner(null), 3000);
    } catch {
      setErrorBanner("Network error: failed to connect to server");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="flex flex-col min-h-full">
      {/* Header */}
      <div className="border-b border-surface-border bg-surface-subnav/50 px-8 py-6">
        <div className="max-w-5xl mx-auto">
          <div className="flex items-center gap-2 text-xs font-mono text-neutral-500 dark:text-neutral-400 mb-2">
            <span>Settings</span>
            <span>/</span>
            <span>User Preferences</span>
            <span>/</span>
            <span className="text-foreground font-medium">Profile & Account</span>
          </div>
          <h1 className="text-xl md:text-2xl font-display font-bold text-foreground tracking-tight">
            Profile & Developer Account
          </h1>
          <p className="text-xs text-neutral-500 dark:text-neutral-400 mt-1.5 font-sans">
            Configure your personal operator identification and session signature.
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
            <div className="flex items-center gap-4">
              <div className="w-16 h-16 rounded-full bg-surface-elevated border-2 border-brand flex items-center justify-center text-foreground font-mono font-bold text-lg">
                AT
              </div>
              <div>
                <h3 className="text-base font-semibold text-foreground">{profile.full_name}</h3>
                <span className="text-xs font-mono text-neutral-500 dark:text-neutral-400">{profile.default_role}</span>
              </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 pt-4 border-t border-surface-borderSubtle">
              <div>
                <label className="block text-xs font-medium text-neutral-700 dark:text-neutral-300 mb-1.5">
                  Full Name
                </label>
                <input
                  type="text"
                  value={profile.full_name}
                  onChange={(e) => setProfile({ ...profile, full_name: e.target.value })}
                  className="w-full px-3 py-2 rounded-lg bg-surface-subnav border border-surface-border text-xs font-mono text-foreground focus:outline-none focus:border-brand"
                />
              </div>

              <div>
                <label className="block text-xs font-medium text-neutral-700 dark:text-neutral-300 mb-1.5">
                  Corporate Email
                </label>
                <input
                  type="email"
                  value={profile.git_author_email}
                  onChange={(e) =>
                    setProfile({ ...profile, git_author_email: e.target.value })
                  }
                  className="w-full px-3 py-2 rounded-lg bg-surface-subnav border border-surface-border text-xs font-mono text-foreground focus:outline-none focus:border-brand"
                />
              </div>

              <div>
                <label className="block text-xs font-medium text-neutral-700 dark:text-neutral-300 mb-1.5">
                  Default Role Designation
                </label>
                <input
                  type="text"
                  value={profile.default_role}
                  onChange={(e) =>
                    setProfile({ ...profile, default_role: e.target.value })
                  }
                  className="w-full px-3 py-2 rounded-lg bg-surface-subnav border border-surface-border text-xs font-mono text-foreground focus:outline-none focus:border-brand"
                />
              </div>
            </div>

            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pt-4 border-t border-surface-borderSubtle">
              <div className="space-y-1">
                <span className="text-sm font-medium text-foreground">
                  Suggest Probable Next Questions
                </span>
                <p className="text-xs text-neutral-500 dark:text-neutral-400">
                  Propose intelligent, context-aware follow-up prompts as semi-transparent clickable text at the end of each chat turn.
                </p>
              </div>
              <label className="relative inline-flex items-center cursor-pointer flex-shrink-0">
                <input
                  type="checkbox"
                  checked={profile.suggest_next_questions !== false}
                  onChange={(e) =>
                    setProfile({ ...profile, suggest_next_questions: e.target.checked })
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
