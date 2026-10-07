"use client";

import React, { useState, useEffect } from "react";
import { RefreshCw, Trash2, Save, CheckCircle2 } from "lucide-react";

export default function UserGitSettingsPage() {
  const [gitPrefs, setGitPrefs] = useState({
    git_author_name: "Alex Turner",
    git_author_email: "alex.turner@acme.internal",
    git_branch_prefix: "alex/rocket-",
    git_personal_pat: "ghp_live_pat_finegrained",
  });
  const [branchSuffix, setBranchSuffix] = useState("feat-*");
  const [saving, setSaving] = useState(false);
  const [successBanner, setSuccessBanner] = useState<string | null>(null);

  useEffect(() => {
    fetch("/v1/settings/user_preferences")
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (data?.effective) {
          setGitPrefs((prev) => ({ ...prev, ...data.effective }));
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
        body: JSON.stringify({ overrides: gitPrefs }),
      });
      setSuccessBanner("Personal Git credentials saved");
      setTimeout(() => setSuccessBanner(null), 3000);
    } catch {
      // ignore
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="flex flex-col min-h-full">
      {/* Scope Summary Banner */}
      <div className="border-b border-surface-border bg-surface-subnav/50 px-8 py-6">
        <div className="max-w-5xl mx-auto">
          <div className="flex items-center gap-2 text-xs font-mono text-neutral-500 dark:text-neutral-400 mb-2">
            <span>Settings</span>
            <span>/</span>
            <span>User Preferences</span>
            <span>/</span>
            <span className="text-foreground font-medium">Personal Git Credentials</span>
          </div>
          <div className="flex items-center gap-3">
            <h1 className="text-xl md:text-2xl font-display font-bold text-foreground tracking-tight">
              Personal Git Credentials & Author Overrides
            </h1>
            <span className="text-[11px] font-mono text-brand bg-brand/10 px-2 py-0.5 rounded border border-brand/20">
              USER LAYER: ACTIVE
            </span>
          </div>
          <p className="text-xs text-neutral-500 dark:text-neutral-400 mt-1.5 font-sans">
            Configure fine-grained personal access tokens and custom branch namespaces for automated workflows.
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

          {/* Linked Personal Account Card */}
          <div className="bg-surface-card rounded-xl border border-surface-border p-5 flex flex-col md:flex-row md:items-center justify-between gap-4">
            <div className="flex items-center gap-3.5">
              <div className="w-10 h-10 rounded-full bg-surface-elevated border border-surface-border flex items-center justify-center text-foreground font-mono font-bold text-sm">
                AT
              </div>
              <div className="space-y-0.5">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-semibold text-foreground">@alex-turner</span>
                  <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-surface-elevated text-neutral-600 dark:text-neutral-300 border border-surface-border">
                    Fine-grained PAT
                  </span>
                </div>
                <div className="flex items-center gap-2 text-xs text-neutral-500 dark:text-neutral-400 font-mono">
                  <span className="flex items-center gap-1 text-emerald-400">
                    <span className="w-1.5 h-1.5 rounded-full bg-emerald-400" />
                    Authorized
                  </span>
                  <span>·</span>
                  <span className="text-neutral-500 dark:text-neutral-400">Expiration: 68 days remaining</span>
                </div>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => {
                  setSuccessBanner("PAT token re-validated");
                  setTimeout(() => setSuccessBanner(null), 3000);
                }}
                className="px-3.5 py-1.5 rounded-lg bg-surface-elevated hover:bg-surface-highlight text-xs font-mono text-foreground border border-surface-border flex items-center gap-1.5 transition-colors cursor-pointer"
              >
                <RefreshCw className="w-3.5 h-3.5 text-brand" />
                <span>Re-authenticate</span>
              </button>
              <button
                type="button"
                className="p-1.5 rounded-lg text-neutral-400 hover:text-rose-400 hover:bg-rose-500/10 border border-transparent hover:border-rose-500/20 transition-colors cursor-pointer"
                title="Revoke PAT"
              >
                <Trash2 className="w-4 h-4" />
              </button>
            </div>
          </div>

          {/* Target Branch Namespace Field */}
          <div className="bg-surface-card rounded-xl border border-surface-border p-5 space-y-2">
            <div className="flex items-center justify-between">
              <label className="text-sm font-medium text-foreground block">
                Target Branch Namespace Prefix
              </label>
              <span className="text-[10px] font-mono text-neutral-400 dark:text-neutral-500">GIT REF PATTERN</span>
            </div>
            <p className="text-xs text-neutral-500 dark:text-neutral-400">
              When Rocket automatically creates feature or fix branches on your behalf, this prefix enforces individual developer ownership.
            </p>
            <div className="pt-1.5 flex items-center max-w-xl">
              <div className="flex items-center w-full rounded-lg bg-surface-subnav border border-surface-border focus-within:border-brand px-3 py-2 transition-all">
                <span className="text-xs font-mono text-neutral-500 select-none">
                  refs/heads/
                </span>
                <input
                  type="text"
                  value={gitPrefs.git_branch_prefix}
                  onChange={(e) =>
                    setGitPrefs({ ...gitPrefs, git_branch_prefix: e.target.value })
                  }
                  className="bg-transparent text-xs font-mono text-brand focus:outline-none w-32 ml-1"
                />
                <input
                  type="text"
                  value={branchSuffix}
                  onChange={(e) => setBranchSuffix(e.target.value)}
                  className="bg-transparent text-xs font-mono text-foreground placeholder-neutral-400 dark:placeholder-neutral-600 focus:outline-none flex-1 ml-1"
                />
              </div>
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
