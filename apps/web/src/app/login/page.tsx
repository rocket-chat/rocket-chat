"use client";

import React, { useState } from "react";
import { signIn } from "next-auth/react";
import { RocketChatLogo } from "../../components/RocketChatLogo";
import { KeyRound, ShieldAlert, ArrowRight } from "lucide-react";

export default function LoginPage() {
  const [username, setUsername] = useState("developer");
  const [orgId, setOrgId] = useState("default_org");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const handleDevSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsSubmitting(true);
    setErrorMsg(null);
    try {
      const result = await signIn("dev-login", {
        username,
        orgId,
        callbackUrl: "/",
        redirect: true,
      });
      if (result?.error) {
        setErrorMsg("Failed to authenticate with provided identity.");
      }
    } catch {
      setErrorMsg("An unexpected connection error occurred.");
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleOidcLogin = () => {
    signIn("oidc", { callbackUrl: "/" });
  };

  const handleGitHubLogin = () => {
    signIn("github", { callbackUrl: "/" });
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-void p-4">
      <div className="w-full max-w-md p-8 rounded-2xl bg-surface-card border border-surface-border shadow-2xl space-y-6">
        <div className="text-center space-y-2">
          <div className="flex justify-center mb-2">
            <RocketChatLogo size="lg" showWordmark={false} />
          </div>
          <h1 className="text-2xl font-display font-bold text-foreground tracking-tight">
            Rocket Chat Cockpit
          </h1>
          <p className="text-xs font-mono text-neutral-500 dark:text-neutral-400">
            Enterprise Autonomous AI Engineering Platform
          </p>
        </div>

        {errorMsg && (
          <div className="flex items-center gap-2 p-3 rounded-lg bg-rose-500/10 border border-rose-500/20 text-rose-400 text-xs font-mono">
            <ShieldAlert className="w-4 h-4 shrink-0" />
            <span>{errorMsg}</span>
          </div>
        )}

        <div className="space-y-3">
          {/* SSO / OAuth Buttons */}
          <button
            type="button"
            onClick={() => signIn("google", { callbackUrl: "/" })}
            className="w-full py-2.5 px-4 rounded-xl bg-surface-elevated hover:bg-surface-border text-foreground border border-surface-border font-medium text-xs flex items-center justify-center gap-2 transition-colors cursor-pointer"
          >
            <svg className="w-4 h-4" viewBox="0 0 24 24">
              <path
                fill="#4285F4"
                d="M23.745 12.27c0-.7-.06-1.4-.19-2.07H12v4.51h6.6c-.29 1.52-1.14 2.82-2.4 3.68v3.05h3.88c2.27-2.09 3.66-5.17 3.66-9.17z"
              />
              <path
                fill="#34A853"
                d="M12 24c3.24 0 5.95-1.08 7.93-2.91l-3.88-3.05c-1.08.72-2.45 1.16-4.05 1.16-3.12 0-5.77-2.1-6.72-4.93H1.25v3.15C3.26 21.36 7.34 24 12 24z"
              />
              <path
                fill="#FBBC05"
                d="M5.28 14.27c-.25-.72-.38-1.49-.38-2.27s.13-1.55.38-2.27V6.58H1.25C.45 8.18 0 9.98 0 12s.45 3.82 1.25 5.42l4.03-3.15z"
              />
              <path
                fill="#EA4335"
                d="M12 4.75c1.77 0 3.35.61 4.6 1.8l3.42-3.42C17.95 1.19 15.24 0 12 0 7.34 0 3.26 2.64 1.25 6.58l4.03 3.15c.95-2.83 3.6-4.98 6.72-4.98z"
              />
            </svg>
            <span>Sign in with Google Workspace</span>
          </button>

          <button
            type="button"
            onClick={handleOidcLogin}
            className="w-full py-2.5 px-4 rounded-xl bg-surface-elevated hover:bg-surface-border text-foreground border border-surface-border font-medium text-xs flex items-center justify-center gap-2 transition-colors cursor-pointer"
          >
            <KeyRound className="w-4 h-4 text-brand" />
            <span>Sign in with Corporate SSO (OIDC)</span>
          </button>

          <button
            type="button"
            onClick={handleGitHubLogin}
            className="w-full py-2.5 px-4 rounded-xl bg-surface-elevated hover:bg-surface-border text-foreground border border-surface-border font-medium text-xs flex items-center justify-center gap-2 transition-colors cursor-pointer"
          >
            <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 24 24">
              <path fillRule="evenodd" clipRule="evenodd" d="M12 2C6.477 2 2 6.484 2 12.017c0 4.425 2.865 8.18 6.839 9.504.5.092.682-.217.682-.483 0-.237-.008-.868-.013-1.703-2.782.605-3.369-1.343-3.369-1.343-.454-1.158-1.11-1.466-1.11-1.466-.908-.62.069-.608.069-.608 1.003.07 1.53 1.032 1.53 1.032.892 1.53 2.341 1.088 2.91.832.092-.647.35-1.088.636-1.338-2.22-.253-4.555-1.113-4.555-4.951 0-1.093.39-1.988 1.029-2.688-.103-.253-.446-1.272.098-2.65 0 0 .84-.27 2.75 1.026A9.564 9.564 0 0112 6.844c.85.004 1.705.115 2.504.337 1.909-1.296 2.747-1.027 2.747-1.027.546 1.379.202 2.398.1 2.651.64.7 1.028 1.595 1.028 2.688 0 3.848-2.339 4.695-4.566 4.943.359.309.678.92.678 1.855 0 1.338-.012 2.419-.012 2.747 0 .268.18.58.688.482A10.019 10.019 0 0022 12.017C22 6.484 17.522 2 12 2z" />
            </svg>
            <span>Sign in with GitHub</span>
          </button>
        </div>

        <div className="relative flex items-center justify-center">
          <div className="border-t border-surface-border w-full" />
          <span className="bg-surface-card px-3 text-[11px] font-mono text-neutral-400 absolute">
            OR DIRECT LOGIN
          </span>
        </div>

        {/* Direct Developer Auth Form */}
        <form onSubmit={handleDevSubmit} className="space-y-4 pt-1">
          <div>
            <label className="block text-xs font-mono text-neutral-500 dark:text-neutral-400 mb-1">
              Developer Identity
            </label>
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="e.g. jdoe"
              required
              className="w-full px-3 py-2 rounded-lg bg-surface-subnav border border-surface-border text-xs font-mono text-foreground focus:outline-none focus:border-brand"
            />
          </div>

          <div>
            <label className="block text-xs font-mono text-neutral-500 dark:text-neutral-400 mb-1">
              Tenant Organization ID
            </label>
            <input
              type="text"
              value={orgId}
              onChange={(e) => setOrgId(e.target.value)}
              placeholder="e.g. acme-corp"
              required
              className="w-full px-3 py-2 rounded-lg bg-surface-subnav border border-surface-border text-xs font-mono text-foreground focus:outline-none focus:border-brand"
            />
          </div>

          <button
            type="submit"
            disabled={isSubmitting}
            className="w-full py-2.5 px-4 rounded-xl bg-brand hover:bg-brand-hover text-white font-medium text-xs flex items-center justify-center gap-2 transition-all shadow-md cursor-pointer disabled:opacity-50"
          >
            <span>{isSubmitting ? "Authenticating..." : "Enter Flight Deck"}</span>
            <ArrowRight className="w-4 h-4" />
          </button>
        </form>
      </div>
    </div>
  );
}
