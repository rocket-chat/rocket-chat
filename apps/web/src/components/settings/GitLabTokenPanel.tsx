"use client";

import React, { useCallback, useEffect, useState } from "react";
import { CheckCircle2, AlertCircle, Trash2 } from "lucide-react";
import { Button } from "../ui/Button";
import { Field, Input } from "../ui/Field";

interface GitLabStatus {
  linked: boolean;
  username: string;
  url: string;
}

/** A GitLab access token: the agent clones and publishes GitLab repositories with it. */
export const GitLabTokenPanel: React.FC = () => {
  const [status, setStatus] = useState<GitLabStatus | null>(null);
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await fetch("/v1/user/credentials/gitlab");
      if (res.ok) setStatus(await res.json());
    } catch {
      // The rest of the page still works without the GitLab section.
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/v1/user/credentials/gitlab", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        setError(typeof body.detail === "string" ? body.detail : "GitLab did not accept this token.");
        return;
      }
      setStatus(await res.json());
      setToken("");
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    setBusy(true);
    try {
      await fetch("/v1/user/credentials/gitlab", { method: "DELETE" });
      await load();
    } finally {
      setBusy(false);
    }
  };

  return (
    <section
      className="p-4 rounded-lg border border-border bg-surface-card space-y-3"
      aria-labelledby="gitlab-token-title"
      data-testid="gitlab-token-panel"
    >
      <h3 id="gitlab-token-title" className="text-xs font-semibold text-foreground">
        GitLab
      </h3>
      <p className="text-xs text-muted-foreground">
        For repositories on {status?.url ?? "GitLab"}: the agent clones them and publishes branches
        and merge requests with this token (scopes <code>read_repository</code>,{" "}
        <code>write_repository</code> and <code>api</code>). The sandbox never receives it.
      </p>
      {status?.linked ? (
        <div className="flex items-center justify-between gap-3">
          <span className="flex items-center gap-2 text-xs text-foreground">
            <CheckCircle2 className="h-4 w-4 text-emerald-500" aria-hidden="true" />
            Token added{status.username ? ` for @${status.username}` : ""}
          </span>
          <Button variant="secondary" onClick={remove} disabled={busy} data-testid="gitlab-token-remove">
            <Trash2 className="w-3.5 h-3.5" aria-hidden="true" /> Remove
          </Button>
        </div>
      ) : (
        <div className="flex items-end gap-2">
          <Field label="GitLab access token" className="flex-1">
            {(control) => (
              <Input
                {...control}
                type="password"
                autoComplete="off"
                value={token}
                onChange={(e) => setToken(e.target.value)}
                placeholder="glpat-…"
                data-testid="gitlab-token-input"
              />
            )}
          </Field>
          <Button onClick={save} disabled={busy || token.trim().length < 8} data-testid="gitlab-token-save">
            Save
          </Button>
        </div>
      )}
      {error && (
        <p role="alert" className="flex items-center gap-2 text-xs text-rose-600 dark:text-rose-400">
          <AlertCircle className="w-4 h-4" aria-hidden="true" /> {error}
        </p>
      )}
    </section>
  );
};
