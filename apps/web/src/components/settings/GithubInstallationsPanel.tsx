"use client";

import React, { useCallback, useEffect, useState } from "react";
import { ExternalLink, Trash2 } from "lucide-react";

interface InstallationList {
  app_configured: boolean;
  install_url: string | null;
  installations: { installation_id: number; account_login: string }[];
}

/** GitHub accounts this organization has installed the Rocket Chat GitHub App on. */
export const GithubInstallationsPanel: React.FC = () => {
  const [data, setData] = useState<InstallationList | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await fetch("/v1/admin/github/installations");
      if (res.ok) setData(await res.json());
      else if (res.status !== 403) setError(`Could not load installations (HTTP ${res.status}).`);
    } catch {
      setError("Could not reach the API.");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const remove = async (id: number) => {
    const res = await fetch(`/v1/admin/github/installations/${id}`, { method: "DELETE" });
    if (res.ok) setData(await res.json());
    else setError("Could not remove the installation.");
  };

  if (!data) return error ? <p className="text-xs text-amber-300">{error}</p> : null;

  return (
    <div
      data-testid="github-installations"
      className="p-4 rounded-lg border border-border bg-surface-card space-y-3"
    >
      <div className="text-xs font-semibold text-foreground">
        GitHub App installations
      </div>
      <p className="text-[11px] text-muted-foreground">
        Agents use the App only on the accounts you install it on here; another organization can
        never use your installation.
      </p>
      {!data.app_configured && (
        <p className="text-[11px] text-amber-300">
          The platform has no GitHub App configured (GITHUB_APP_ID / GITHUB_APP_PRIVATE_KEY).
        </p>
      )}
      <ul className="space-y-1">
        {data.installations.map((i) => (
          <li
            key={i.installation_id}
            data-testid="github-installation"
            className="flex items-center justify-between text-xs text-neutral-300"
          >
            <span>
              {i.account_login} <span className="text-neutral-500">#{i.installation_id}</span>
            </span>
            <button
              type="button"
              aria-label={`Remove installation ${i.account_login}`}
              onClick={() => void remove(i.installation_id)}
              className="p-1 rounded-lg hover:bg-surface-elevated text-neutral-400 hover:text-red-400 cursor-pointer"
            >
              <Trash2 className="w-3.5 h-3.5" />
            </button>
          </li>
        ))}
        {data.installations.length === 0 && data.app_configured && (
          <li className="text-[11px] text-neutral-500">No installation is mapped yet.</li>
        )}
      </ul>
      {data.install_url && (
        <a
          href={data.install_url}
          data-testid="github-install-link"
          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-brand/40 bg-brand/10 text-brand text-xs font-semibold"
        >
          <ExternalLink className="w-3.5 h-3.5" /> INSTALL ON A GITHUB ACCOUNT
        </a>
      )}
      {data.app_configured && !data.install_url && (
        <p className="text-[11px] text-neutral-500">
          Set GITHUB_APP_SLUG to enable the install link.
        </p>
      )}
      {error && <p className="text-xs text-amber-300">{error}</p>}
    </div>
  );
};
