"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useMissionStore } from "../lib/store";

export default function MissionControlPage() {
  const router = useRouter();
  const { session, fetchSessions, createSession } = useMissionStore();

  useEffect(() => {
    let isMounted = true;

    const navigateToCanonicalChat = async () => {
      // If already in an active session, go straight to it
      if (session?.session_id) {
        router.replace(`/chat/${session.session_id}`);
        return;
      }

      await fetchSessions();
      if (!isMounted) return;

      const currentSessions = useMissionStore.getState().sessions;
      if (currentSessions.length > 0) {
        router.replace(`/chat/${currentSessions[0].session_id}`);
      } else {
        const newSession = await createSession();
        if (newSession && isMounted) {
          router.replace(`/chat/${newSession.session_id}`);
        }
      }
    };

    navigateToCanonicalChat();

    return () => {
      isMounted = false;
    };
  }, [session?.session_id, fetchSessions, createSession, router]);

  return (
    <div className="h-screen w-screen flex flex-col items-center justify-center bg-void text-foreground font-mono text-xs gap-3">
      <div className="w-8 h-8 rounded-xl border border-primary/40 bg-primary/10 flex items-center justify-center text-primary font-bold animate-pulse">
        RC
      </div>
      <div className="text-muted-foreground flex items-center gap-2">
        <span className="w-2 h-2 rounded-full bg-primary animate-ping" />
        <span>CONNECTING TO MISSION TELEMETRY DECK...</span>
      </div>
    </div>
  );
}
