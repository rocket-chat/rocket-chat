"use client";

import React, { use } from "react";
import dynamic from "next/dynamic";
import { SessionLoadingScreen } from "../../../components/SessionLoadingScreen";

const CockpitLayout = dynamic(
  () => import("../../../components/CockpitLayout").then((mod) => mod.CockpitLayout),
  {
    ssr: false,
    loading: () => (
      <div className="h-full w-full flex items-center justify-center bg-void text-foreground">
        <SessionLoadingScreen statusMessage="Loading session telemetry..." />
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
