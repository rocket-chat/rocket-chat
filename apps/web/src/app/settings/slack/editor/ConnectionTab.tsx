"use client";

import React, { useEffect, useState } from "react";
import { Copy, Download, RefreshCw } from "lucide-react";
import { Field, Input } from "../../../../components/ui/Field";
import type { BotEditorState } from "./useBotEditor";

function useManifest(botId: string | undefined): { manifest: string | null; loading: boolean } {
  const [manifest, setManifest] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    if (!botId) return;
    setLoading(true);
    fetch(`/v1/admin/slack/bots/${botId}/manifest`)
      .then((res) => (res.ok ? res.json() : null))
      .then((data: unknown) => data && setManifest(JSON.stringify(data, null, 2)))
      .catch(() => undefined)
      .finally(() => setLoading(false));
  }, [botId]);
  return { manifest, loading };
}

const ConnectionTest: React.FC<{ botId: string }> = ({ botId }) => {
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null);
  const test = async () => {
    setTesting(true);
    setResult(null);
    try {
      const res = await fetch(`/v1/admin/slack/bots/${botId}/test`, { method: "POST" });
      const data = await res.json();
      setResult(
        res.ok && data.ok
          ? { ok: true, message: `Connected to workspace "${data.team_name}" as @${data.handle}` }
          : { ok: false, message: data.detail || "Connection test failed." },
      );
    } catch (err) {
      setResult({ ok: false, message: err instanceof Error ? err.message : "Network error" });
    } finally {
      setTesting(false);
    }
  };
  return (
    <div className="flex items-center gap-3 pt-2">
      <button
        type="button"
        onClick={() => void test()}
        disabled={testing}
        data-testid="test-connection-button"
        className="flex items-center gap-1.5 rounded-lg border border-border bg-surface-elevated px-3 py-1.5 text-xs text-foreground disabled:opacity-50"
      >
        <RefreshCw className={`h-3.5 w-3.5 ${testing ? "animate-spin" : ""}`} aria-hidden="true" />
        <span>Test connection (auth.test)</span>
      </button>
      {result && (
        <p role="status" className={`text-xs ${result.ok ? "text-emerald-600 dark:text-emerald-400" : "text-rose-600 dark:text-rose-400"}`}>
          {result.message}
        </p>
      )}
    </div>
  );
};

const Manifest: React.FC<{ manifest: string; loading: boolean; fileName: string }> = ({ manifest, loading, fileName }) => {
  const download = () => {
    const url = URL.createObjectURL(new Blob([manifest], { type: "application/json" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = fileName;
    link.click();
    URL.revokeObjectURL(url);
  };
  const action = "flex items-center gap-1 rounded border border-border bg-surface-elevated px-2.5 py-1 text-[11px] text-foreground hover:bg-surface-subnav";
  return (
    <div className="space-y-2 border-t border-border/60 pt-4">
      <div className="flex items-center justify-between">
        <div>
          <span className="text-xs font-semibold text-foreground">Slack App Manifest</span>
          <p className="text-[10px] text-muted-foreground">Paste this manifest into api.slack.com to configure scopes and Socket Mode events.</p>
        </div>
        <div className="flex items-center gap-2">
          <button type="button" onClick={() => void navigator.clipboard.writeText(manifest)} className={action}>
            <Copy className="h-3 w-3" aria-hidden="true" /> <span>Copy</span>
          </button>
          <button type="button" onClick={download} className={action}>
            <Download className="h-3 w-3" aria-hidden="true" /> <span>Download</span>
          </button>
        </div>
      </div>
      <pre className="max-h-48 overflow-y-auto rounded-lg border border-surface-border bg-surface-subnav p-3 font-mono text-[10px] text-foreground">
        {loading ? "Generating manifest..." : manifest}
      </pre>
    </div>
  );
};

/** Name, slash command, the two Slack tokens, a connection test and the app manifest. */
export const ConnectionTab: React.FC<{ editor: BotEditorState }> = ({ editor }) => {
  const { bot, form, patch } = editor;
  const { manifest, loading } = useManifest(bot?.id);
  return (
    <div className="space-y-5">
      <Field label="Bot Display Name" hint="How this bot appears in Rocket Chat cockpit.">
        {(props) => (
          <Input {...props} data-testid="bot-name-input" value={form.name} onChange={(e) => patch({ name: e.target.value })} placeholder="e.g. Senior Backend Assistant" />
        )}
      </Field>
      <Field
        label="Slash command"
        hint={`Empty: named after the bot (${bot?.command || "from its Slack name"}). Each bot needs its own: Slack sends a command two apps share to the one installed last. Copy the manifest into the Slack app after changing it.`}
      >
        {(props) => (
          <Input
            {...props}
            data-testid="bot-command-input"
            value={form.slashCommand}
            onChange={(e) => patch({ slashCommand: e.target.value })}
            placeholder={bot?.command || "/rocky"}
          />
        )}
      </Field>
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <Field label="Bot User OAuth Token" hint={bot?.bot_token_fingerprint ? `Stored (${bot.bot_token_fingerprint})` : "Starts with xoxb-"}>
          {(props) => (
            <Input
              {...props}
              type="password"
              data-testid="bot-token-input"
              value={form.botToken}
              onChange={(e) => patch({ botToken: e.target.value })}
              placeholder={bot?.bot_token_fingerprint ? "•••••••• (unchanged)" : "xoxb-..."}
            />
          )}
        </Field>
        <Field label="App-Level Token (Socket Mode)" hint={bot?.app_token_fingerprint ? `Stored (${bot.app_token_fingerprint})` : "Starts with xapp-"}>
          {(props) => (
            <Input
              {...props}
              type="password"
              data-testid="app-token-input"
              value={form.appToken}
              onChange={(e) => patch({ appToken: e.target.value })}
              placeholder={bot?.app_token_fingerprint ? "•••••••• (unchanged)" : "xapp-..."}
            />
          )}
        </Field>
      </div>
      {bot && <ConnectionTest botId={bot.id} />}
      {bot && manifest && <Manifest manifest={manifest} loading={loading} fileName={`${form.name.toLowerCase().replace(/\s+/g, "-")}-manifest.json`} />}
    </div>
  );
};
