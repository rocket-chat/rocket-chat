"use client";

import React, { useState, useEffect } from "react";
import { useSession } from "next-auth/react";
import {
  Key,
  Eye,
  EyeOff,
  Save,
  CheckCircle2,
  AlertCircle,
  Building2,
  Lock,
} from "lucide-react";

interface OrgGeneralState {
  company_name: string;
  compliance_tier: string;
  session_privacy_default: string;
  telemetry_level: string;
  thrust_animation_enabled: boolean;
  require_signed_commits: boolean;
  suggest_next_questions: boolean;
  sso_allowed_domains?: string;
  sso_enforce_domain_match?: boolean;
}

export default function OrgSettingsPage() {
  const { data: session } = useSession();
  const userRoles = (session?.user as unknown as { roles?: string[] })?.roles || [];
  const isAdmin = userRoles.includes("admin") || userRoles.includes("owner");
  const tenantOrg = (session?.user as unknown as { orgId?: string })?.orgId || "default_org";

  const [keys, setKeys] = useState({
    openrouter: "",
    anthropic: "",
    openai: "",
    github_pat: "",
  });

  const [orgGeneral, setOrgGeneral] = useState<OrgGeneralState>({
    company_name: "Engineering Org",
    compliance_tier: "SOC2",
    session_privacy_default: "private",
    telemetry_level: "standard",
    thrust_animation_enabled: true,
    require_signed_commits: true,
    suggest_next_questions: true,
    sso_allowed_domains: "",
    sso_enforce_domain_match: false,
  });

  const [visibleKey, setVisibleKey] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [successBanner, setSuccessBanner] = useState<string | null>(null);
  const [errorBanner, setErrorBanner] = useState<string | null>(null);

  useEffect(() => {
    fetch("/v1/settings/general")
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (data?.effective) {
          setOrgGeneral((prev) => ({ ...prev, ...data.effective }));
        }
      })
      .catch(() => {});
  }, []);

  const toggleVisibility = (provider: string) => {
    setVisibleKey(visibleKey === provider ? null : provider);
  };

  const handleSave = async () => {
    setSaving(true);
    setErrorBanner(null);
    setSuccessBanner(null);
    try {
      const res = await fetch("/v1/admin/settings/general", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          config: orgGeneral,
          locked_keys: ["compliance_tier", "require_signed_commits"],
        }),
      });

      if (!res.ok) {
        let errMessage = "";
        try {
          const data = await res.json();
          errMessage = data.detail || data.message || JSON.stringify(data);
        } catch {
          errMessage = await res.text();
        }
        throw new Error(errMessage || `Request failed with HTTP ${res.status}`);
      }

      setSuccessBanner("Organization policies and credentials updated");
      setTimeout(() => setSuccessBanner(null), 3000);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to update organization settings";
      setErrorBanner(msg);
      setTimeout(() => setErrorBanner(null), 6000);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="flex flex-col min-h-full">
      {/* Scope Summary Banner */}
      <div className="border-b border-surface-border bg-surface-subnav/50 px-8 py-6">
        <div className="max-w-5xl mx-auto flex flex-col md:flex-row md:items-center justify-between gap-4">
          <div>
            <div className="flex items-center gap-2 text-xs font-mono text-neutral-500 dark:text-neutral-400 mb-2">
              <span>Settings</span>
              <span>/</span>
              <span>Organization</span>
              <span>/</span>
              <span className="text-foreground font-medium">General & Compliance</span>
            </div>
            <div className="flex flex-wrap items-center gap-3">
              <h1 className="text-xl md:text-2xl font-display font-bold text-foreground tracking-tight">
                General & Compliance Policies
              </h1>
              <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-mono bg-emerald-950/80 text-emerald-400 border border-emerald-800/50">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
                SOC2 Type II Enforced
              </span>
            </div>
            <p className="text-xs text-neutral-500 dark:text-neutral-400 mt-1.5 font-sans">
              Manage enterprise tenant compliance constraints, cryptographic secrets, and system telemetry rules.
            </p>
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

          {!isAdmin && (
            <div className="flex items-center justify-between p-3.5 rounded-xl bg-amber-950/30 border border-amber-800/40 text-amber-300 text-xs font-mono">
              <div className="flex items-center gap-2">
                <Lock className="w-4 h-4 text-amber-400 shrink-0" />
                <span>Read-Only View: Organization-wide policies require an Administrator or Owner role to modify.</span>
              </div>
              <span className="text-[10px] uppercase font-bold bg-amber-500/10 px-2 py-0.5 rounded border border-amber-500/20">
                ROLE: {userRoles[0] || "user"}
              </span>
            </div>
          )}

          {/* Org Identification & Governance */}
          <section className="space-y-4">
            <div className="flex items-center justify-between pb-2 border-b border-surface-border">
              <div className="flex items-center gap-2.5">
                <div className="w-6 h-6 rounded bg-brand/10 border border-brand/20 flex items-center justify-center text-brand">
                  <Building2 className="w-3.5 h-3.5" />
                </div>
                <h2 className="text-base font-display font-semibold text-foreground">
                  Tenant Governance
                </h2>
              </div>
              <span className="text-[11px] font-mono text-neutral-500 dark:text-neutral-400 bg-surface-card px-2 py-0.5 rounded border border-surface-border">
                ORG-ID: {tenantOrg}
              </span>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div className="bg-surface-card rounded-xl border border-surface-border p-5 space-y-2">
                <label className="text-xs font-medium text-foreground block">Organization Name</label>
                <input
                  type="text"
                  value={orgGeneral.company_name}
                  onChange={(e) =>
                    setOrgGeneral({ ...orgGeneral, company_name: e.target.value })
                  }
                  className="w-full px-3 py-2 rounded-lg bg-surface-subnav border border-surface-border text-xs font-mono text-foreground focus:outline-none focus:border-brand"
                />
              </div>

              <div className="bg-surface-card rounded-xl border border-surface-border p-5 space-y-2">
                <div className="flex items-center justify-between">
                  <label className="text-xs font-medium text-foreground block">Compliance Standard</label>
                  <span className="text-[10px] font-mono text-brand bg-brand/10 px-1.5 py-0.5 rounded border border-brand/20">
                    LOCKED
                  </span>
                </div>
                <select
                  value={orgGeneral.compliance_tier}
                  disabled
                  className="w-full px-3 py-2 rounded-lg bg-surface-subnav border border-surface-border text-xs font-mono text-neutral-500 dark:text-neutral-400 focus:outline-none cursor-not-allowed opacity-80"
                >
                  <option value="SOC2">SOC2 Type II (Continuous Auditing)</option>
                  <option value="HIPAA">HIPAA (BAA Enforced)</option>
                  <option value="ISO27001">ISO 27001 (ISMS Validated)</option>
                </select>
              </div>
            </div>

            {/* SSO & Domain Restriction Policy */}
            <div className="bg-surface-card rounded-xl border border-surface-border p-5 space-y-4 mt-4">
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                <div>
                  <span className="text-sm font-medium text-foreground block">
                    SSO & Corporate Email Domain Allowlist
                  </span>
                  <p className="text-xs text-neutral-500 dark:text-neutral-400 mt-0.5">
                    Restrict Single Sign-On (Google Workspace, OIDC) strictly to users with specific email domains (e.g. <code>company.com, birota.io</code>).
                  </p>
                </div>
                <label className="relative inline-flex items-center cursor-pointer flex-shrink-0">
                  <input
                    type="checkbox"
                    checked={Boolean(orgGeneral.sso_enforce_domain_match)}
                    onChange={(e) =>
                      setOrgGeneral({ ...orgGeneral, sso_enforce_domain_match: e.target.checked })
                    }
                    className="sr-only peer"
                  />
                  <div className="w-11 h-6 bg-surface-highlight peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-brand" />
                </label>
              </div>

              <div>
                <label className="block text-xs font-mono text-neutral-500 dark:text-neutral-400 mb-1">
                  Allowed Domains (Comma-Separated)
                </label>
                <input
                  type="text"
                  value={orgGeneral.sso_allowed_domains || ""}
                  onChange={(e) =>
                    setOrgGeneral({ ...orgGeneral, sso_allowed_domains: e.target.value })
                  }
                  placeholder="e.g. birota.io, acme.com (leave empty to allow all organization members)"
                  className="w-full px-3 py-2 rounded-lg bg-surface-subnav border border-surface-border text-xs font-mono text-foreground focus:outline-none focus:border-brand"
                />
              </div>
            </div>

            {/* Suggestion Toggle Row */}
            <div className="bg-surface-card rounded-xl border border-surface-border p-5 flex flex-col sm:flex-row sm:items-center justify-between gap-4 mt-4">
              <div className="space-y-1 max-w-2xl">
                <span className="text-sm font-medium text-foreground">
                  Suggest Probable Next Questions
                </span>
                <p className="text-xs text-neutral-500 dark:text-neutral-400 leading-relaxed">
                  Propose intelligent, context-aware follow-up prompts at the end of agent turns as discreet clickable text.
                </p>
              </div>
              <label className="relative inline-flex items-center cursor-pointer flex-shrink-0">
                <input
                  type="checkbox"
                  checked={orgGeneral.suggest_next_questions}
                  onChange={(e) =>
                    setOrgGeneral({ ...orgGeneral, suggest_next_questions: e.target.checked })
                  }
                  className="sr-only peer"
                />
                <div className="w-11 h-6 bg-surface-highlight peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-brand" />
              </label>
            </div>
          </section>

          {/* BYOK Encrypted API Vault */}
          <section className="space-y-4">
            <div className="flex items-center justify-between pb-2 border-b border-surface-border">
              <div className="flex items-center gap-2.5">
                <div className="w-6 h-6 rounded bg-neutral-200 dark:bg-neutral-800 border border-surface-border flex items-center justify-center text-neutral-700 dark:text-neutral-300">
                  <Key className="w-3.5 h-3.5" />
                </div>
                <h2 className="text-base font-display font-semibold text-foreground">
                  Encrypted BYOK Vault
                </h2>
              </div>
              <span className="text-[10px] font-mono text-emerald-400 bg-emerald-950/60 px-2 py-0.5 rounded border border-emerald-800/40">
                AES-GCM-256
              </span>
            </div>

            <div className="bg-surface-card rounded-xl border border-surface-border p-5 space-y-4">
              <div>
                <label className="block text-xs font-mono text-neutral-700 dark:text-neutral-300 mb-1">
                  OpenRouter Primary API Key
                </label>
                <div className="relative">
                  <input
                    type={visibleKey === "openrouter" ? "text" : "password"}
                    value={keys.openrouter}
                    onChange={(e) => setKeys({ ...keys, openrouter: e.target.value })}
                    className="w-full pl-3 pr-10 py-2 rounded-lg border border-surface-border bg-surface-subnav text-xs font-mono text-foreground focus:outline-none focus:border-brand"
                  />
                  <button
                    type="button"
                    onClick={() => toggleVisibility("openrouter")}
                    className="absolute right-3 top-2.5 text-neutral-400 hover:text-foreground"
                  >
                    {visibleKey === "openrouter" ? (
                      <EyeOff className="w-3.5 h-3.5" />
                    ) : (
                      <Eye className="w-3.5 h-3.5" />
                    )}
                  </button>
                </div>
              </div>

              <div>
                <label className="block text-xs font-mono text-neutral-700 dark:text-neutral-300 mb-1">
                  Anthropic API Key (Direct Fallback)
                </label>
                <div className="relative">
                  <input
                    type={visibleKey === "anthropic" ? "text" : "password"}
                    value={keys.anthropic}
                    placeholder="sk-ant-api03-..."
                    onChange={(e) => setKeys({ ...keys, anthropic: e.target.value })}
                    className="w-full pl-3 pr-10 py-2 rounded-lg border border-surface-border bg-surface-subnav text-xs font-mono text-foreground focus:outline-none focus:border-brand"
                  />
                  <button
                    type="button"
                    onClick={() => toggleVisibility("anthropic")}
                    className="absolute right-3 top-2.5 text-neutral-400 hover:text-foreground"
                  >
                    {visibleKey === "anthropic" ? (
                      <EyeOff className="w-3.5 h-3.5" />
                    ) : (
                      <Eye className="w-3.5 h-3.5" />
                    )}
                  </button>
                </div>
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
