"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useMissionStore } from "../lib/store";

export default function MissionControlPage() {
  const router = useRouter();
  const { session, initDraftSession } = useMissionStore();

  useEffect(() => {
    // 1. If store already has active persisted session, navigate immediately
    if (
      session?.session_id &&
      !session.is_draft &&
      session.session_id !== "draft" &&
      session.session_id !== "new"
    ) {
      router.replace(`/chat/${session.session_id}`);
      return;
    }

    // 2. Check cached last session id in localStorage for instant restore
    try {
      const cachedId =
        typeof window !== "undefined" ? localStorage.getItem("rocket_last_session_id") : null;
      if (cachedId && cachedId !== "draft" && cachedId !== "new") {
        router.replace(`/chat/${cachedId}`);
        return;
      }
    } catch {}

    // 3. Immediately route to draft session so cockpit renders instantly with zero blocking delay
    initDraftSession();
    router.replace(`/chat/draft`);
  }, [session?.session_id, session?.is_draft, initDraftSession, router]);

  return (
    <div className="h-full w-full flex flex-col items-center justify-center bg-void text-foreground font-mono text-xs gap-3">
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
