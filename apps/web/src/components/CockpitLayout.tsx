"use client";

import React, { useEffect, useRef } from "react";
import { PanelGroup, Panel, PanelResizeHandle } from "react-resizable-panels";
import { FlightTelemetryHeader } from "./FlightTelemetryHeader";
import { FlightLogStream } from "./FlightLogStream";
import { IgnitionConsole } from "./IgnitionConsole";
import { MonacoDiffViewer } from "./MonacoDiffViewer";
import { StreamingTerminal } from "./StreamingTerminal";
import { SettingsModal } from "./settings/SettingsModal";
import { SessionSidebar } from "./Sidebar/SessionSidebar";
import { MissionWebSocket } from "../lib/websocket";
import { useMissionStore } from "../lib/store";
import { GripVertical } from "lucide-react";

interface CockpitLayoutProps {
  sessionId?: string;
}

export const CockpitLayout: React.FC<CockpitLayoutProps> = ({ sessionId }) => {
  const wsRef = useRef<MissionWebSocket | null>(null);
  const {
    session,
    sessions,
    setSession,
    isRightPanelOpen,
    fetchSessions,
    fetchAgents,
    fetchModels,
  } = useMissionStore();

  // Load session list, models, and agent personas on mount
  useEffect(() => {
    fetchSessions();
    fetchAgents();
    fetchModels();
  }, [fetchSessions, fetchAgents, fetchModels]);

  // Sync session from URL parameter if provided
  useEffect(() => {
    if (!sessionId) return;
    if (session?.session_id === sessionId) return;

    const matched = sessions.find((s) => s.session_id === sessionId);
    if (matched) {
      setSession(matched);
    }
    fetch(`/v1/sessions/${sessionId}`)
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (data && data.session_id === sessionId) {
          setSession(data);
        }
      })
      .catch(() => {});
  }, [sessionId, sessions, session?.session_id, setSession]);

  // Connect or reconnect WebSocket when active session changes
  useEffect(() => {
    const activeSessionId = sessionId || session?.session_id;
    if (!activeSessionId) return;

    if (wsRef.current) {
      wsRef.current.disconnect();
      wsRef.current = null;
    }

    const ws = new MissionWebSocket(activeSessionId);
    ws.connect();
    wsRef.current = ws;

    return () => {
      ws.disconnect();
      wsRef.current = null;
    };
  }, [sessionId, session?.session_id]);

  const handleIgnite = (prompt: string, model?: string) => {
    wsRef.current?.sendTurn(prompt, model);
  };

  const handleAnswerQuestion = (questionId: string, selectedOptions: string[], customText?: string) => {
    wsRef.current?.answerQuestion(questionId, selectedOptions, customText);
  };

  const handleApprove = (actionId: string) => {
    wsRef.current?.submitApproval(actionId, true);
  };

  const handleReject = (actionId: string) => {
    wsRef.current?.submitApproval(actionId, false);
  };

  return (
    <div className="h-screen w-screen flex flex-col bg-surface-base text-foreground font-sans overflow-hidden">
      {/* Top Application Header */}
      <FlightTelemetryHeader />

      {/* Main Cockpit Layout with Collapsible Session Sidebar */}
      <div className="flex-1 flex overflow-hidden">
        {/* Left Navigation Sidebar */}
        <SessionSidebar />

        {/* Cockpit Split Pane */}
        <div className="flex-1 flex overflow-hidden">
          <PanelGroup direction="horizontal" className="h-full w-full">
            {/* Center Panel: Continuous Flight Stream & Ignition Console */}
            <Panel
              id="flight-stream-panel"
              order={1}
              defaultSize={isRightPanelOpen ? 50 : 100}
              minSize={30}
              className="flex flex-col h-full bg-surface-base"
            >
              <FlightLogStream onAnswerQuestion={handleAnswerQuestion} />
              <IgnitionConsole onIgnite={handleIgnite} />
            </Panel>

            {/* Draggable Divider Handle */}
            {isRightPanelOpen && (
              <PanelResizeHandle className="w-1.5 bg-border/60 hover:bg-cyan/50 transition-colors flex items-center justify-center cursor-col-resize group relative select-none">
                <div className="h-8 w-1 rounded-full bg-muted-foreground/40 group-hover:bg-cyan group-hover:shadow-[0_0_8px_rgba(0,242,254,0.6)] transition-all flex items-center justify-center">
                  <GripVertical className="w-3 h-3 text-transparent group-hover:text-cyan/80" />
                </div>
              </PanelResizeHandle>
            )}

            {/* Right Panel: Monaco Diff Matrix & Streaming Terminal */}
            {isRightPanelOpen && (
              <Panel
                id="telemetry-inspector-panel"
                order={2}
                defaultSize={50}
                minSize={25}
                collapsible={true}
                className="flex flex-col h-full overflow-hidden bg-void"
              >
                {/* Top half: Monaco Diff Viewer */}
                <div className="h-3/5 overflow-hidden">
                  <MonacoDiffViewer onApprove={handleApprove} onReject={handleReject} />
                </div>

                {/* Bottom half: Streaming Terminal */}
                <div className="h-2/5 overflow-hidden border-t border-border">
                  <StreamingTerminal />
                </div>
              </Panel>
            )}
          </PanelGroup>
        </div>
      </div>

      {/* Modal Settings fallback */}
      <SettingsModal />
    </div>
  );
};
