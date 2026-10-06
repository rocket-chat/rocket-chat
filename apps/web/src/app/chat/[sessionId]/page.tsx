"use client";

import React, { use } from "react";
import dynamic from "next/dynamic";

const CockpitLayout = dynamic(
  () => import("../../../components/CockpitLayout").then((mod) => mod.CockpitLayout),
  {
    ssr: false,
    loading: () => (
      <div className="h-screen w-screen flex flex-col items-center justify-center bg-void text-foreground font-mono text-xs gap-3">
        <div className="w-8 h-8 rounded-xl border border-primary/40 bg-primary/10 flex items-center justify-center text-primary font-bold animate-pulse">
          RC
        </div>
        <div className="text-muted-foreground flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-primary animate-ping" />
          <span>LOADING SESSION TELEMETRY...</span>
        </div>
      </div>
    ),
  }
);

interface ChatPageProps {
  params: Promise<{ sessionId: string }>;
}

export default function ChatSessionPage({ params }: ChatPageProps) {
  const resolvedParams = use(params);
  return <CockpitLayout key={resolvedParams.sessionId} sessionId={resolvedParams.sessionId} />;
}
