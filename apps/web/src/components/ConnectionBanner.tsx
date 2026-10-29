"use client";

import React, { useEffect, useState } from "react";
import { WifiOff, RefreshCw } from "lucide-react";
import { useMissionStore } from "../lib/store";

/** How long the live connection may be down before the banner says so (a reconnect is usually faster). */
const GRACE_MS = 1500;

/**
 * Says when the live view is not receiving updates: "Offline" while the browser has no
 * network, "Reconnecting…" while the socket retries. Hidden until the socket has connected
 * once, so opening a session does not flash it.
 */
export const ConnectionBanner: React.FC = () => {
  const connected = useMissionStore((s) => s.connected);
  const [everConnected, setEverConnected] = useState(false);
  const [visible, setVisible] = useState(false);
  const [online, setOnline] = useState(() => (typeof navigator === "undefined" ? true : navigator.onLine));

  useEffect(() => {
    const update = () => setOnline(navigator.onLine);
    window.addEventListener("online", update);
    window.addEventListener("offline", update);
    return () => {
      window.removeEventListener("online", update);
      window.removeEventListener("offline", update);
    };
  }, []);

  useEffect(() => {
    if (connected) {
      setEverConnected(true);
      setVisible(false);
      return;
    }
    if (!everConnected) return;
    const timer = setTimeout(() => setVisible(true), GRACE_MS);
    return () => clearTimeout(timer);
  }, [connected, everConnected]);

  if (!visible) return null;
  return (
    <div
      role="status"
      aria-live="polite"
      data-testid="connection-banner"
      className="flex items-center justify-center gap-2 border-b border-amber-500/40 bg-amber-500/10 px-4 py-1.5 text-xs text-amber-800 dark:text-amber-200"
    >
      {online ? (
        <>
          <RefreshCw className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
          Reconnecting… live updates are paused and will resume where they stopped.
        </>
      ) : (
        <>
          <WifiOff className="h-3.5 w-3.5" aria-hidden="true" />
          Offline: live updates resume when the network is back.
        </>
      )}
    </div>
  );
};
