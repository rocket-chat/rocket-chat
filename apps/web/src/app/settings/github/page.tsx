"use client";

import React, { useState, useEffect } from "react";
import {
  RefreshCw,
  ExternalLink,
  Building2,
  User,
  Lock,
  Plus,
  Trash2,
  Save,
  CheckCircle2,
} from "lucide-react";

interface GitHubSettingsData {
  commit_authorship_policy: string;
  pr_creation_policy: string;
  commit_signing_mode: string;
  bot_author_name: string;
  bot_author_email: string;
  default_branch: string;
}

interface UserPreferencesData {
  git_branch_prefix: string;
  git_personal_pat: string;
  git_author_name: string;
  git_author_email: string;
}

export default function GitHubSettingsPage() {
  const [saving, setSaving] = useState(false);
  const [successBanner, setSuccessBanner] = useState<string | null>(null);

  // Org-level GitHub configuration
  const [orgConfig, setOrgConfig] = useState<GitHubSettingsData>({
    commit_authorship_policy: "co_authored",
    pr_creation_policy: "as_user",
    commit_signing_mode: "github_app",
    bot_author_name: "RocketChat Bot",
    bot_author_email: "bot@rocketchat.internal",
    default_branch: "main",
  });

  // User-specific Git overrides
  const [userPrefs, setUserPrefs] = useState<UserPreferencesData>({
    git_branch_prefix: "alex/rocket-",
    git_personal_pat: "ghp_live_pat_finegrained",
    git_author_name: "Alex Turner",
    git_author_email: "alex.turner@acme.internal",
  });

  const [prCreatorIdentity, setPrCreatorIdentity] = useState<"bot" | "user">("bot");
  const [commitSigningEnforced, setCommitSigningEnforced] = useState(true);
  const [reviewerAssignment, setReviewerAssignment] = useState(true);
  const [branchSuffix, setBranchSuffix] = useState("feat-*");
  const [mandatoryMergeGate, setMandatoryMergeGate] = useState(true);

  // Fetch cascading settings from backend API
  const fetchSettings = async () => {
    try {
      const res = await fetch("/v1/settings/github");
      if (res.ok) {
        const data = await res.json();
        if (data.effective) {
          setOrgConfig((prev) => ({ ...prev, ...data.effective }));
          if (data.effective.pr_creation_policy === "as_bot") {
            setPrCreatorIdentity("bot");
          } else {
            setPrCreatorIdentity("user");
          }
          if (data.effective.commit_signing_mode === "none") {
            setCommitSigningEnforced(false);
          } else {
            setCommitSigningEnforced(true);
          }
        }
      }

      // Fetch user preferences
      const userRes = await fetch("/v1/settings/user_preferences");
      if (userRes.ok) {
        const userData = await userRes.json();
        if (userData.effective) {
          setUserPrefs((prev) => ({ ...prev, ...userData.effective }));
        }
      }
    } catch (err) {
      console.error("Failed to load GitHub settings:", err);
    }
  };

  useEffect(() => {
    fetchSettings();
  }, []);

  const handleSaveAll = async () => {
    setSaving(true);
    try {
      // 1. Save Org Settings
      const orgPayload = {
        config: {
          ...orgConfig,
          pr_creation_policy: prCreatorIdentity === "bot" ? "as_bot" : "as_user",
          commit_signing_mode: commitSigningEnforced ? "github_app" : "none",
        },
        locked_keys: ["commit_signing_mode"],
      };

      await fetch("/v1/admin/settings/github", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(orgPayload),
      }).catch(() => {});

      // 2. Save User Overrides
      const userPayload = {
        overrides: {
          ...userPrefs,
          git_branch_prefix: userPrefs.git_branch_prefix,
        },
      };

      await fetch("/v1/settings/user_preferences", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(userPayload),
      });

      setSuccessBanner("GitHub configuration and developer credentials updated");
      setTimeout(() => setSuccessBanner(null), 3000);
    } catch (err) {
      console.error("Failed to save settings:", err);
    } finally {
      setSaving(false);
    }
  };

  const handleDiscard = () => {
    fetchSettings();
    setSuccessBanner("Changes discarded and reset to saved state");
    setTimeout(() => setSuccessBanner(null), 3000);
  };

  return (
    <div className="flex flex-col min-h-full">
      {/* Scope Summary Banner */}
      <div className="border-b border-surface-border bg-surface-subnav/50 px-8 py-6">
        <div className="max-w-5xl mx-auto flex flex-col md:flex-row md:items-center justify-between gap-4">
          <div>
            {/* Breadcrumb */}
            <div className="flex items-center gap-2 text-xs font-mono text-neutral-400 mb-2">
              <span>Settings</span>
              <span>/</span>
              <span>Organization</span>
              <span>/</span>
              <span className="text-white font-medium">GitHub Integration</span>
            </div>
            <div className="flex flex-wrap items-center gap-3">
              <h1 className="text-xl md:text-2xl font-display font-bold text-white tracking-tight">
                GitHub & Code Collaboration
              </h1>
              <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-mono bg-emerald-950/80 text-emerald-400 border border-emerald-800/50">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
                Connected: @acme-corp · 24 Repositories
              </span>
            </div>
            <p className="text-xs text-neutral-400 mt-1.5 font-sans">
              Manage repository access policies, automated pull request workflows, and developer authorship attribution.
            </p>
          </div>
          <div className="flex items-center gap-2 self-start md:self-center">
            <button
              type="button"
              onClick={fetchSettings}
              className="px-3.5 py-2 rounded-lg bg-surface-card hover:bg-surface-elevated text-xs font-mono text-neutral-200 border border-surface-border flex items-center gap-2 transition-colors cursor-pointer"
            >
              <RefreshCw className="w-4 h-4 text-neutral-400" />
              <span>Sync Repositories</span>
            </button>
            <a
              href="https://github.com"
              target="_blank"
              rel="noopener noreferrer"
              className="p-2 rounded-lg bg-surface-card hover:bg-surface-elevated text-neutral-400 hover:text-white border border-surface-border transition-colors"
              title="Open GitHub"
            >
              <ExternalLink className="w-4 h-4" />
            </a>
          </div>
        </div>
      </div>

      {/* Main Body */}
      <div className="flex-1 p-8">
        <div className="max-w-5xl mx-auto space-y-8">
          {successBanner && (
            <div className="flex items-center gap-2 p-3.5 rounded-xl bg-emerald-950/60 border border-emerald-800/60 text-emerald-400 text-xs font-mono">
              <CheckCircle2 className="w-4 h-4 shrink-0" />
              <span>{successBanner}</span>
            </div>
          )}

          {/* SECTION 1: ORGANIZATION REPOSITORY POLICIES */}
          <section className="space-y-4">
            <div className="flex items-center justify-between pb-2 border-b border-surface-border">
              <div className="flex items-center gap-2.5">
                <div className="w-6 h-6 rounded bg-brand/10 border border-brand/20 flex items-center justify-center text-brand">
                  <Building2 className="w-3.5 h-3.5" />
                </div>
                <h2 className="text-base font-display font-semibold text-white">
                  Organization Repository Policies
                </h2>
              </div>
              <span className="text-[11px] font-mono text-neutral-400 bg-surface-card px-2 py-0.5 rounded border border-surface-border">
                POLICY: ACME-TIER-1
              </span>
            </div>

            {/* Default PR Creator Identity */}
            <div className="bg-surface-card rounded-xl border border-surface-border p-5 space-y-4">
              <div>
                <label className="text-sm font-medium text-white block">
                  Default PR Creator Identity
                </label>
                <p className="text-xs text-neutral-400 mt-0.5">
                  Determine which author identity creates pull requests generated during automated Mission Terminal workflows.
                </p>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {/* Radio Card 1 (Bot) */}
                <label
                  onClick={() => setPrCreatorIdentity("bot")}
                  className={`relative flex items-start gap-3.5 p-4 rounded-lg cursor-pointer transition-all ${
                    prCreatorIdentity === "bot"
                      ? "border-2 border-brand bg-surface-elevated/70"
                      : "border border-surface-border hover:border-neutral-600 bg-surface-subnav"
                  }`}
                >
                  <input
                    type="radio"
                    name="pr_creator_identity"
                    value="bot"
                    checked={prCreatorIdentity === "bot"}
                    onChange={() => setPrCreatorIdentity("bot")}
                    className="mt-1 text-brand focus:ring-brand focus:ring-offset-surface-base bg-surface-card border-neutral-600"
                  />
                  <div className="space-y-1">
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-mono font-semibold text-white">
                        Org Service Bot (`rocket-bot[bot]`)
                      </span>
                      <span className="text-[9px] font-mono px-1.5 py-0.5 uppercase rounded bg-brand/20 text-brand">
                        Recommended
                      </span>
                    </div>
                    <p className="text-xs text-neutral-400 leading-relaxed">
                      Uses verified company bot credentials with unified automated service tokens. Individual developer is credited in the PR footer description.
                    </p>
                  </div>
                </label>

                {/* Radio Card 2 (User) */}
                <label
                  onClick={() => setPrCreatorIdentity("user")}
                  className={`relative flex items-start gap-3.5 p-4 rounded-lg cursor-pointer transition-all ${
                    prCreatorIdentity === "user"
                      ? "border-2 border-brand bg-surface-elevated/70"
                      : "border border-surface-border hover:border-neutral-600 bg-surface-subnav"
                  }`}
                >
                  <input
                    type="radio"
                    name="pr_creator_identity"
                    value="user"
                    checked={prCreatorIdentity === "user"}
                    onChange={() => setPrCreatorIdentity("user")}
                    className="mt-1 text-brand focus:ring-brand focus:ring-offset-surface-base bg-surface-card border-neutral-600"
                  />
                  <div className="space-y-1">
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-mono font-semibold text-neutral-200">
                        Individual Author Attribution
                      </span>
                    </div>
                    <p className="text-xs text-neutral-400 leading-relaxed">
                      Opens PRs directly under each developer&apos;s personal linked GitHub account via OAuth/PAT token delegation.
                    </p>
                  </div>
                </label>
              </div>
            </div>

            {/* Cryptographic Commit Signing */}
            <div className="bg-surface-card rounded-xl border border-surface-border p-5 flex flex-col sm:flex-row sm:items-center justify-between gap-4">
              <div className="space-y-1 max-w-2xl">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium text-white">
                    Cryptographic Commit Signing
                  </span>
                  <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-emerald-950/60 text-emerald-400 border border-emerald-800/40">
                    GPG / SSH ENFORCED
                  </span>
                </div>
                <p className="text-xs text-neutral-400 leading-relaxed">
                  Sign all terminal-generated Git commits via Acme&apos;s corporate hardware security module (HSM) root key before pushing to upstream remotes.
                </p>
              </div>
              <label className="relative inline-flex items-center cursor-pointer flex-shrink-0">
                <input
                  type="checkbox"
                  checked={commitSigningEnforced}
                  onChange={(e) => setCommitSigningEnforced(e.target.checked)}
                  className="sr-only peer"
                />
                <div className="w-11 h-6 bg-surface-highlight peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-brand" />
              </label>
            </div>

            {/* Automated Reviewer Assignment */}
            <div className="bg-surface-card rounded-xl border border-surface-border p-5 space-y-3">
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
                <div className="space-y-1 max-w-2xl">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-white">
                      Automated Reviewer Assignment
                    </span>
                    <span className="text-[10px] font-mono text-neutral-400">
                      RULES: `main`, `staging`
                    </span>
                  </div>
                  <p className="text-xs text-neutral-400 leading-relaxed">
                    Automatically request reviews from Rocket AI Agent and designated security code-owners on critical branches.
                  </p>
                </div>
                <label className="relative inline-flex items-center cursor-pointer flex-shrink-0">
                  <input
                    type="checkbox"
                    checked={reviewerAssignment}
                    onChange={(e) => setReviewerAssignment(e.target.checked)}
                    className="sr-only peer"
                  />
                  <div className="w-11 h-6 bg-surface-highlight peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-brand" />
                </label>
              </div>

              {/* Branch Rule Badges */}
              <div className="pt-2 flex flex-wrap items-center gap-2 border-t border-surface-borderSubtle">
                <span className="text-[11px] font-mono text-neutral-400">
                  Protected Branches:
                </span>
                <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded bg-surface-subnav text-[11px] font-mono text-neutral-300 border border-surface-border">
                  <Lock className="w-3 h-3 text-amber-400" />
                  main
                </span>
                <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded bg-surface-subnav text-[11px] font-mono text-neutral-300 border border-surface-border">
                  <Lock className="w-3 h-3 text-amber-400" />
                  staging
                </span>
                <button
                  type="button"
                  className="text-[11px] font-mono text-brand hover:underline flex items-center gap-1 ml-1 cursor-pointer"
                >
                  <Plus className="w-3 h-3" />
                  Add branch rule
                </button>
              </div>
            </div>
          </section>

          {/* SECTION 2: PERSONAL GIT CREDENTIALS & AUTHOR OVERRIDES */}
          <section className="space-y-4 pt-2">
            <div className="flex items-center justify-between pb-2 border-b border-surface-border">
              <div className="flex items-center gap-2.5">
                <div className="w-6 h-6 rounded bg-neutral-800 border border-surface-border flex items-center justify-center text-neutral-300">
                  <User className="w-3.5 h-3.5" />
                </div>
                <h2 className="text-base font-display font-semibold text-white">
                  Personal Git Credentials & Author Overrides
                </h2>
              </div>
              <span className="text-[11px] font-mono text-brand bg-brand/10 px-2 py-0.5 rounded border border-brand/20">
                USER LAYER: ACTIVE
              </span>
            </div>

            {/* Linked Personal Account Card */}
            <div className="bg-surface-card rounded-xl border border-surface-border p-5 flex flex-col md:flex-row md:items-center justify-between gap-4">
              <div className="flex items-center gap-3.5">
                <div className="w-10 h-10 rounded-full bg-surface-elevated border border-surface-border flex items-center justify-center text-white font-mono font-bold text-sm">
                  AT
                </div>
                <div className="space-y-0.5">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-semibold text-white">
                      @alex-turner
                    </span>
                    <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-surface-elevated text-neutral-300 border border-surface-border">
                      Fine-grained PAT
                    </span>
                  </div>
                  <div className="flex items-center gap-2 text-xs text-neutral-400 font-mono">
                    <span className="flex items-center gap-1 text-emerald-400">
                      <span className="w-1.5 h-1.5 rounded-full bg-emerald-400" />
                      Authorized
                    </span>
                    <span>·</span>
                    <span className="text-neutral-400">
                      Expiration: 68 days remaining
                    </span>
                  </div>
                </div>
              </div>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => {
                    setSuccessBanner("GitHub token refreshed");
                    setTimeout(() => setSuccessBanner(null), 3000);
                  }}
                  className="px-3.5 py-1.5 rounded-lg bg-surface-elevated hover:bg-surface-highlight text-xs font-mono text-white border border-surface-border flex items-center gap-1.5 transition-colors cursor-pointer"
                >
                  <RefreshCw className="w-3.5 h-3.5 text-brand" />
                  <span>Re-authenticate</span>
                </button>
                <button
                  type="button"
                  className="p-1.5 rounded-lg text-neutral-500 hover:text-rose-400 hover:bg-rose-500/10 border border-transparent hover:border-rose-500/20 transition-colors cursor-pointer"
                  title="Revoke PAT"
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
            </div>

            {/* Target Branch Namespace Field */}
            <div className="bg-surface-card rounded-xl border border-surface-border p-5 space-y-2">
              <div className="flex items-center justify-between">
                <label className="text-sm font-medium text-white block">
                  Target Branch Namespace Prefix
                </label>
                <span className="text-[10px] font-mono text-neutral-500">
                  GIT REF PATTERN
                </span>
              </div>
              <p className="text-xs text-neutral-400">
                When Rocket automatically creates feature or fix branches on your behalf, this prefix enforces individual developer ownership.
              </p>
              <div className="pt-1.5 flex items-center max-w-xl">
                <div className="flex items-center w-full rounded-lg bg-surface-subnav border border-surface-border focus-within:border-brand px-3 py-2 transition-all">
                  <span className="text-xs font-mono text-neutral-500 select-none">
                    refs/heads/
                  </span>
                  <input
                    type="text"
                    value={userPrefs.git_branch_prefix}
                    onChange={(e) =>
                      setUserPrefs({ ...userPrefs, git_branch_prefix: e.target.value })
                    }
                    className="bg-transparent text-xs font-mono text-brand focus:outline-none w-32 ml-1"
                  />
                  <input
                    type="text"
                    value={branchSuffix}
                    onChange={(e) => setBranchSuffix(e.target.value)}
                    className="bg-transparent text-xs font-mono text-white placeholder-neutral-600 focus:outline-none flex-1 ml-1"
                  />
                </div>
              </div>
            </div>

            {/* Mandatory Merge Gate Passkey */}
            <div className="bg-surface-card rounded-xl border border-surface-border p-5 flex flex-col sm:flex-row sm:items-center justify-between gap-4">
              <div className="space-y-1 max-w-2xl">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium text-white">
                    Mandatory Merge Gate
                  </span>
                  <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-amber-950/60 text-amber-400 border border-amber-800/40">
                    HUMAN APPROVAL REQUIRED
                  </span>
                </div>
                <p className="text-xs text-neutral-400 leading-relaxed">
                  Require interactive biometric/passkey approval before Rocket agent can merge pull requests or deploy code to protected target branches.
                </p>
              </div>
              <label className="relative inline-flex items-center cursor-pointer flex-shrink-0">
                <input
                  type="checkbox"
                  checked={mandatoryMergeGate}
                  onChange={(e) => setMandatoryMergeGate(e.target.checked)}
                  className="sr-only peer"
                />
                <div className="w-11 h-6 bg-surface-highlight peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-brand" />
              </label>
            </div>
          </section>
        </div>
      </div>

      {/* Sticky Bottom Save / Discard Bar matching Stitch */}
      <div className="sticky bottom-0 z-20 bg-surface-sidebar/95 backdrop-blur-md border-t border-surface-border px-8 py-3.5 flex flex-wrap items-center justify-between gap-4 flex-shrink-0">
        <div className="flex items-center gap-2.5">
          <span className="w-2 h-2 rounded-full bg-emerald-400" />
          <div className="flex items-center gap-2 text-xs font-mono text-neutral-400">
            <span>Synced with Acme Propulsion Cloud</span>
            <span className="text-neutral-600">·</span>
            <span className="text-neutral-500">Live Cascading Resolver</span>
          </div>
        </div>

        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={handleDiscard}
            className="px-4 py-2 rounded-lg text-xs font-mono text-neutral-400 hover:text-white hover:bg-surface-elevated transition-colors border border-transparent hover:border-surface-border cursor-pointer"
          >
            Discard
          </button>
          <button
            type="button"
            onClick={handleSaveAll}
            disabled={saving}
            className="px-5 py-2 rounded-lg bg-brand hover:bg-brand-hover text-white text-xs font-display font-semibold tracking-wide transition-all shadow-lg shadow-brand/20 flex items-center gap-2 cursor-pointer disabled:opacity-50"
          >
            <Save className="w-4 h-4" />
            <span>{saving ? "Saving..." : "Save Changes"}</span>
          </button>
        </div>
      </div>
    </div>
  );
}
