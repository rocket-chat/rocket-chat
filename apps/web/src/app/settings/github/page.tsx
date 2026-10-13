"use client";

import React, { useState, useEffect, Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { SchemaSettingsForm } from "../../../components/settings/SchemaSettingsForm";
import {
  ExternalLink,
  CheckCircle2,
  AlertCircle,
  Key,
  Trash2,
  Eye,
  EyeOff,
  GitPullRequest,
} from "lucide-react";
import { GithubInstallationsPanel } from "../../../components/settings/GithubInstallationsPanel";
import {
  UserScopeSection,
  ScopeDivider,
  OrgScopeSection,
} from "../../../components/settings/ScopedSettingsSection";

interface GitHubCredentialStatus {
  is_linked: boolean;
  username?: string | null;
  avatar_url?: string | null;
  scopes?: string[];
  auth_type?: string;
  updated_at?: number | null;
  injection_mode: string;
}

function GitHubSettingsContent() {
  const searchParams = useSearchParams();
  const urlStatus = searchParams?.get("status");
  const urlError = searchParams?.get("error");

  const [successBanner, setSuccessBanner] = useState<string | null>(
    urlStatus === "linked" ? "GitHub account linked successfully!" : null
  );
  const [errorBanner, setErrorBanner] = useState<string | null>(
    urlError ? `GitHub authentication error: ${urlError}` : null
  );

  // User Scope state
  const [credStatus, setCredStatus] = useState<GitHubCredentialStatus | null>(null);
  const [patInput, setPatInput] = useState("");
  const [showPat, setShowPat] = useState(false);
  const [testingPat, setTestingPat] = useState(false);

  // Load User settings
  const loadUserData = async () => {
    try {
      const credRes = await fetch("/v1/user/credentials/github");
      if (credRes.ok) {
        const data: GitHubCredentialStatus = await credRes.json();
        setCredStatus(data);
      }
    } catch (e) {
      console.warn("Failed fetching user git settings:", e);
    }
  };

  useEffect(() => {
    loadUserData();
  }, []);

  const handleSavePat = async () => {
    if (!patInput.trim()) return;
    setTestingPat(true);
    setErrorBanner(null);
    try {
      const res = await fetch("/v1/user/credentials/github/pat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pat: patInput.trim() }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || "Invalid PAT token");
      }
      const data = await res.json();
      setCredStatus(data);
      setPatInput("");
      setSuccessBanner(`Verified PAT token as @${data.username}`);
      setTimeout(() => setSuccessBanner(null), 3500);
    } catch (err: unknown) {
      setErrorBanner(err instanceof Error ? err.message : "Error validating PAT");
      setTimeout(() => setErrorBanner(null), 5000);
    } finally {
      setTestingPat(false);
    }
  };

  const handleUnlinkUserGit = async () => {
    if (!confirm("Unlink your personal GitHub credentials?")) return;
    try {
      const res = await fetch("/v1/user/credentials/github", { method: "DELETE" });
      if (res.ok) {
        setCredStatus({
          is_linked: false,
          username: null,
          avatar_url: null,
          scopes: [],
          auth_type: "none",
          injection_mode: "app_readonly",
        });
        setSuccessBanner("GitHub credentials unlinked");
        setTimeout(() => setSuccessBanner(null), 3000);
      }
    } catch {
      setErrorBanner("Failed to unlink GitHub credentials");
    }
  };

  return (
    <div className="max-w-5xl mx-auto space-y-6 px-4 py-8 sm:px-8">
      {/* Header */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 pb-4 border-b border-border/70">
        <div>
          <h1 className="font-display text-xl font-semibold tracking-tight text-foreground flex items-center gap-2.5">
            <GitPullRequest className="w-5 h-5 text-brand" />
            Git &amp; version control
          </h1>
          <p className="max-w-2xl text-sm leading-relaxed text-neutral-600 dark:text-neutral-400 mt-1">
            Personal Git identity, PAT tokens, and organization-wide GitHub App governance policies.
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

      {/* TIER 1: USER-SPECIFIC GIT CONFIGURATION */}
      <UserScopeSection
        title="Personal Git Credentials & Identity"
        description="Your committer signature and personal access credentials used when sandbox sessions push code."
      >
        <div className="space-y-4">
          {/* Linked Account Status */}
          <div className="p-4 rounded-lg border border-border bg-surface-card flex flex-col sm:flex-row sm:items-center justify-between gap-4">
            <div className="flex items-center gap-3">
              <div className="p-2.5 rounded-lg bg-surface-elevated border border-border text-neutral-300">
                <Key className="w-5 h-5 text-brand" />
              </div>
              <div>
                <div className="flex items-center gap-2">
                  <span className="text-xs font-semibold text-foreground">
                    {credStatus?.is_linked ? `@${credStatus.username}` : "No GitHub Account Linked"}
                  </span>
                  <span
                    className={`px-1.5 py-0.2 rounded-lg text-[9px] border ${
                      credStatus?.is_linked
                        ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/20"
                        : "bg-neutral-500/10 text-neutral-400 border-neutral-500/20"
                    }`}
                  >
                    {credStatus?.is_linked ? "AUTHENTICATED" : "UNLINKED"}
                  </span>
                </div>
                <p className="text-[11px] text-muted-foreground mt-0.5">
                  {credStatus?.is_linked
                    ? `Mode: ${credStatus.auth_type} • Injection: ${credStatus.injection_mode}`
                    : "Connect via OAuth or provide a Personal Access Token below."}
                </p>
              </div>
            </div>

            <div className="flex items-center gap-2">
              {credStatus?.is_linked ? (
                <button
                  type="button"
                  onClick={handleUnlinkUserGit}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-rose-800/60 bg-rose-950/30 text-rose-400 hover:bg-rose-950/60 text-xs transition-colors cursor-pointer"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                  <span>Unlink</span>
                </button>
              ) : (
                <a
                  href="/api/auth/github/login?link_only=true&redirect_uri=/settings/github"
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-brand/40 bg-brand/10 hover:bg-brand/20 text-brand text-xs font-semibold transition-all shadow-sm"
                >
                  <ExternalLink className="w-3.5 h-3.5" />
                  <span>Connect with GitHub</span>
                </a>
              )}
            </div>
          </div>

          {/* Committer identity & branch prefix (member-scoped registry settings) */}
          <SchemaSettingsForm
            domain="user_preferences"
            mode="user"
            title="Committer signature & branch prefix"
            description="Who commits are attributed to and how the agent names its branches. Organization policy decides whether the bot or you is the committer."
            only={["git_author_name", "git_author_email", "git_branch_prefix"]}
          />

          {/* Personal Access Token (PAT) Fallback */}
          <div className="p-4 rounded-lg border border-border bg-surface-card space-y-3">
            <div className="text-xs font-semibold text-foreground">
              Personal Access Token (PAT)
            </div>
            <p className="text-[11px] text-muted-foreground">
              Provide a fine-grained or classic token with <code>repo</code> permissions to push branches from sandboxes.
            </p>
            <div className="flex gap-2">
              <div className="relative flex-1">
                <input
                  type={showPat ? "text" : "password"}
                  placeholder="ghp_..."
                  value={patInput}
                  onChange={(e) => setPatInput(e.target.value)}
                  className="w-full pl-3 pr-10 py-1.5 rounded-lg border border-border bg-void text-xs text-foreground focus:outline-none focus:border-brand"
                />
                <button
                  type="button"
                  onClick={() => setShowPat(!showPat)}
                  className="absolute right-3 top-2 text-neutral-400 hover:text-white"
                >
                  {showPat ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
                </button>
              </div>
              <button
                type="button"
                onClick={handleSavePat}
                disabled={testingPat || !patInput.trim()}
                className="px-3 py-1.5 rounded-lg border border-border bg-surface-elevated hover:bg-surface-elevated/80 text-xs text-neutral-300 transition-all cursor-pointer disabled:opacity-50"
              >
                {testingPat ? "VALIDATING..." : "SAVE & VERIFY PAT"}
              </button>
            </div>
          </div>
        </div>
      </UserScopeSection>

      {/* VISUAL SEPARATOR */}
      <ScopeDivider label="Organization Scope" />

      {/* TIER 2: ORGANIZATION-WIDE GIT CONFIGURATION */}
      <OrgScopeSection
        title="Organization GitHub App Policies"
        description="Workspace-wide Git governance: commit authorship and signing, pull request identity, and the permissions of GitHub App tokens."
      >
        <SchemaSettingsForm
          domain="github"
          mode="org"
          title="Git governance"
          description="Applied to every agent commit and pull request in this organization."
        />
        <GithubInstallationsPanel />
      </OrgScopeSection>
    </div>
  );
}

export default function GitHubSettingsPage() {
  return (
    <Suspense fallback={<div className="p-8 text-xs text-muted-foreground">Loading settings...</div>}>
      <GitHubSettingsContent />
    </Suspense>
  );
}
