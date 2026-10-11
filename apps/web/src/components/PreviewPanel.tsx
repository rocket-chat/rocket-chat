"use client";

import React, { useCallback, useEffect, useState } from "react";
import { ExternalLink, RefreshCw } from "lucide-react";
import { useMissionStore } from "../lib/store";

/**
 * A live view of an app the agent started in the sandbox (`localhost:<port>` over there).
 * The frame is sandboxed without `allow-same-origin`: the app is the agent's own output, so
 * it runs in an opaque origin with no access to the cockpit's cookies or storage.
 */
export const PreviewPanel: React.FC<{ request?: { port: number; nonce: number } | null }> = ({
  request = null,
}) => {
  const session = useMissionStore((s) => s.session);
  const sessionId = session && !session.is_draft ? session.session_id : null;
  const [port, setPort] = useState("3000");
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [frameKey, setFrameKey] = useState(0);

  const openPort = useCallback(
    async (target: string) => {
    if (!sessionId) return;
    setError(null);
    const res = await fetch(`/v1/sessions/${sessionId}/preview`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ port: Number(target) }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      const detail = Array.isArray(body.detail) ? "Enter a port between 1024 and 65535." : body.detail;
      setError(typeof detail === "string" ? detail : `HTTP ${res.status}`);
      return;
    }
    setUrl((await res.json()).url);
    setFrameKey((key) => key + 1);
      },
    [sessionId]
  );

  // A process row asked to show its port: open it without another click.
  useEffect(() => {
    if (!request) return;
    setPort(String(request.port));
    void openPort(String(request.port));
  }, [request, openPort]);

  const open = (event?: React.FormEvent) => {
    event?.preventDefault();
    void openPort(port);
  };

  if (!sessionId) {
    return <p className="p-3 text-xs text-muted-foreground">Start a session to preview its app.</p>;
  }

  return (
    <div data-testid="preview-panel" className="h-full flex flex-col min-h-0">
      <form onSubmit={open} className="flex items-center gap-2 px-2 py-1 border-b border-border text-xs font-mono">
        <label className="flex items-center gap-1 text-neutral-400">
          localhost:
          <input
            data-testid="preview-port"
            inputMode="numeric"
            value={port}
            onChange={(e) => setPort(e.target.value.replace(/\D/g, ""))}
            className="w-16 px-1 py-0.5 rounded border border-border bg-void text-foreground"
          />
        </label>
        <button type="submit" data-testid="preview-open" className="px-2 py-0.5 rounded border border-border hover:bg-surface-elevated cursor-pointer">
          Open
        </button>
        {url && (
          <>
            <button type="button" aria-label="Reload preview" onClick={() => setFrameKey(frameKey + 1)} className="p-1 rounded hover:bg-surface-elevated cursor-pointer">
              <RefreshCw className="w-3.5 h-3.5" />
            </button>
            <a href={url} target="_blank" rel="noopener noreferrer" aria-label="Open preview in a new tab" className="p-1 rounded hover:bg-surface-elevated">
              <ExternalLink className="w-3.5 h-3.5" />
            </a>
          </>
        )}
        {error && <span role="alert" data-testid="preview-error" className="text-amber-300">{error}</span>}
      </form>
      <div className="flex-1 min-h-0 bg-white">
        {url ? (
          <iframe
            key={frameKey}
            data-testid="preview-frame"
            title="App preview"
            src={url}
            sandbox="allow-scripts allow-forms allow-popups allow-modals"
            className="w-full h-full border-0"
          />
        ) : (
          <p className="p-3 text-xs text-neutral-600">
            Ask the agent to start the app (for example on port 3000), then open it here. Apps that
            use root-relative URLs work for HTML and CSS; start module bundlers with a relative base.
          </p>
        )}
      </div>
    </div>
  );
};
