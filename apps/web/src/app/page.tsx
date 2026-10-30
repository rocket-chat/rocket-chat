"use client";

import { useShallow } from "zustand/react/shallow";
import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useMissionStore } from "../lib/store";
import { SessionLoadingScreen } from "../components/SessionLoadingScreen";

export default function MissionControlPage() {
  const router = useRouter();
  const { session, initDraftSession } = useMissionStore(
    useShallow((state) => ({ session: state.session, initDraftSession: state.initDraftSession }))
  );

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
    <div className="h-full w-full flex items-center justify-center bg-void text-foreground">
      <SessionLoadingScreen statusMessage="Opening a new conversation…" />
    </div>
  );
}
