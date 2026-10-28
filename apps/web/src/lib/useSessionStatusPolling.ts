import { useEffect } from "react";
import { useMissionStore } from "./store";
import { SidebarSocket } from "./sidebar-socket";

/** How often the list is re-read while the live connection is down and the page is in view. */
export const SESSION_STATUS_POLL_MS = 6000;

/**
 * Keeps the sidebar's session status live for every session, not only the open one: the open
 * session's socket carries its own events, and the sidebar socket every listed one's. The list is
 * read again whenever that socket (re)connects, and re-read on a timer only while it is down, so a
 * dropped connection degrades to the old polling rather than to a stale list.
 */
export function useSessionStatusPolling(token: string): void {
  const refresh = useMissionStore((state) => state.refreshSessionStatuses);
  const applySessionUpdate = useMissionStore((state) => state.applySessionUpdate);

  useEffect(() => {
    const socket = new SidebarSocket(token, {
      onReady: () => void refresh(),
      onUpdate: applySessionUpdate,
    });
    socket.connect();
    const pollWhileDisconnected = () => {
      if (document.visibilityState === "visible" && !socket.isOpen()) void refresh();
    };
    // A tab shown again may have slept through a reconnect: read once whatever the socket says.
    const refreshIfVisible = () => {
      if (document.visibilityState === "visible") void refresh();
    };
    const timer = setInterval(pollWhileDisconnected, SESSION_STATUS_POLL_MS);
    document.addEventListener("visibilitychange", refreshIfVisible);
    window.addEventListener("focus", refreshIfVisible);
    return () => {
      socket.disconnect();
      clearInterval(timer);
      document.removeEventListener("visibilitychange", refreshIfVisible);
      window.removeEventListener("focus", refreshIfVisible);
    };
  }, [token, refresh, applySessionUpdate]);
}
