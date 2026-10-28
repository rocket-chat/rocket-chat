"use client";

import React, { useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import { PanelGroup, Panel, PanelResizeHandle } from "react-resizable-panels";
import { FlightTelemetryHeader } from "./FlightTelemetryHeader";
import { FlightLogStream } from "./FlightLogStream";
import { IgnitionConsole } from "./IgnitionConsole";
import { InspectorTop } from "./InspectorTop";
import { useSessionStatusPolling } from "../lib/useSessionStatusPolling";
import { StreamingTerminal } from "./StreamingTerminal";
import { SessionSidebar } from "./Sidebar/SessionSidebar";
import { TurnInspector } from "./TurnInspector";
import { MemoryInspector } from "./MemoryInspector";
import { ArtifactsDrawer } from "./ArtifactsDrawer";
import { MissionWebSocket } from "../lib/websocket";
import { newMessageId } from "../lib/message-id";
import { useMissionStore } from "../lib/store";
import { useAuth } from "./AuthProvider";
import { GripVertical, X } from "lucide-react";

interface CockpitLayoutProps {
  sessionId?: string;
}

export const CockpitLayout: React.FC<CockpitLayoutProps> = ({ sessionId }) => {
  const router = useRouter();
  const wsRef = useRef<MissionWebSocket | null>(null);
  const session = useMissionStore((s) => s.session);
  const hydratedSessionId = useMissionStore((s) => s.hydratedSessionId);
  // The URL names a conversation whose full record has not arrived: text typed now would be saved
  // under whatever was open before, and a sidebar row has no transcript to show.
  const isTargetSession = !!sessionId && sessionId !== "draft" && sessionId !== "new";
  const opening = isTargetSession && (hydratedSessionId !== sessionId || session?.session_id !== sessionId);
  const setSession = useMissionStore((s) => s.setSession);
  const isRightPanelOpen = useMissionStore((s) => s.isRightPanelOpen);
  const fetchSessions = useMissionStore((s) => s.fetchSessions);
  const fetchAgents = useMissionStore((s) => s.fetchAgents);
  const fetchModels = useMissionStore((s) => s.fetchModels);
  const initDraftSession = useMissionStore((s) => s.initDraftSession);
  const createSessionWithPrompt = useMissionStore((s) => s.createSessionWithPrompt);

  const terminalOpen = useMissionStore((s) => s.terminalOpen);
  const terminalActivity = useMissionStore((s) => s.terminalActivity);
  const setTerminalOpen = useMissionStore((s) => s.setTerminalOpen);
  // Shown once something has printed (or when asked for): an empty terminal is wasted room.
  const terminalExpanded = terminalOpen ?? terminalActivity;
  const toggleTerminal = () => setTerminalOpen(!terminalExpanded);

  const [isMobile, setIsMobile] = React.useState(false);

  const setIsLeftSidebarOpen = useMissionStore((s) => s.setIsLeftSidebarOpen);

  useEffect(() => {
    const checkMobile = () => {
      setIsMobile(window.innerWidth < 768);
    };
    checkMobile();
    // On a phone the sidebar is a drawer: start with the conversation, not the list.
    if (window.innerWidth < 768) setIsLeftSidebarOpen(false);
    window.addEventListener("resize", checkMobile);
    return () => window.removeEventListener("resize", checkMobile);
  }, [setIsLeftSidebarOpen]);

  // Opening a conversation from the drawer closes it.
  useEffect(() => {
    if (window.innerWidth < 768) setIsLeftSidebarOpen(false);
  }, [sessionId, setIsLeftSidebarOpen]);

  // Load session list, models, and agent personas on mount
  useEffect(() => {
    fetchSessions();
    fetchAgents();
    fetchModels();
  }, [fetchSessions, fetchAgents, fetchModels]);

  // Sync session from URL parameter if provided
  useEffect(() => {
    if (!sessionId || sessionId === "draft" || sessionId === "new") {
      if (!session || !session.is_draft) {
        initDraftSession();
      }
      return;
    }
    if (hydratedSessionId === sessionId) return;

    // The socket's snapshot is the baseline (it alone carries a running turn); this read only
    // shows the transcript sooner when the socket is slow, so it asks for the recent tail.
    fetch(`/v1/sessions/${sessionId}?tail=50`)
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        // Arriving after the snapshot, it would rewind the transcript live events built on.
        if (useMissionStore.getState().hydratedSessionId === sessionId) return;
        if (data && data.session_id === sessionId) {
          setSession(data);
        }
      })
      .catch(() => {});
  }, [sessionId, session, hydratedSessionId, setSession, initDraftSession]);

  const { user } = useAuth();
  const token = user?.accessToken || "";
  useSessionStatusPolling(token);
  const activeSessionId = sessionId || session?.session_id;
  const isDraft = !activeSessionId || activeSessionId === "draft" || activeSessionId === "new" || session?.is_draft;

  // Connect or reconnect WebSocket only when target active session or token changes
  useEffect(() => {
    if (isDraft || !activeSessionId) {
      if (wsRef.current) {
        wsRef.current.disconnect();
        wsRef.current = null;
      }
      return;
    }

    // Preserve existing live socket if already targeting this session and token
    if (wsRef.current) {
      if (
        wsRef.current.getSessionId() === activeSessionId &&
        wsRef.current.getToken() === token &&
        wsRef.current.isConnectedOrConnecting()
      ) {
        return;
      }
      wsRef.current.disconnect();
      wsRef.current = null;
    }

    const ws = new MissionWebSocket(activeSessionId, undefined, token);
    ws.connect();
    wsRef.current = ws;

    return () => {
      ws.disconnect();
      wsRef.current = null;
    };
  }, [activeSessionId, token, isDraft]);

  const handleIgnite = async (
    prompt: string,
    model?: string,
    messageId?: string,
    attachmentIds?: string[],
    delivery: "steer" | "queue" = "steer",
    files?: File[],
    reasoningEffort?: string
  ) => {
    if (isDraft) {
      const newSession = await createSessionWithPrompt(prompt, model, messageId, files);
      if (newSession) {
        router.push(`/chat/${newSession.session_id}`);
      }
      return;
    }
    wsRef.current?.sendTurn(prompt, model, messageId, attachmentIds, delivery, reasoningEffort);
  };

  const handleCancelPending = (messageId: string) => {
    wsRef.current?.cancelPendingMessage(messageId);
  };

  const handleCancel = () => {
    wsRef.current?.cancelTurn();
  };

  const handleRetryTurn = (prompt: string) => {
    // Recovery for an interrupted turn: re-dispatch its prompt as a fresh turn,
    // recording the optimistic entry so the echoed broadcast dedupes against it.
    const messageId = newMessageId();
    useMissionStore.getState().appendOwnUserMessage({
      messageId,
      content: prompt,
      authorUserId: user?.user_id ?? null,
    });
    wsRef.current?.sendTurn(prompt, undefined, messageId);
  };

  const handleAnswerQuestion = (questionId: string, selectedOptions: string[], customText?: string) => {
    wsRef.current?.answerQuestion(questionId, selectedOptions, customText);
  };

  const handleApprove = (actionId: string) => {
    wsRef.current?.submitApproval(actionId, true);
    // Clear the gate optimistically; the server confirms via decision_submitted.
    useMissionStore.getState().setActiveApproval(null);
  };

  const handleReject = (actionId: string) => {
    wsRef.current?.submitApproval(actionId, false);
    useMissionStore.getState().setActiveApproval(null);
  };

  return (
    <div className="h-full w-full flex flex-col bg-surface-base text-foreground font-sans overflow-hidden">
      {/* Top Application Header */}
      <FlightTelemetryHeader />

      {/* Main Cockpit Layout with Collapsible Session Sidebar */}
      <div className="flex-1 flex overflow-hidden">
        {/* Left Navigation Sidebar */}
        <SessionSidebar />

        {/* Cockpit Split Pane */}
        <div className="flex-1 flex overflow-hidden">
          {isMobile ? (
            <div className="flex min-h-0 flex-col h-full w-full bg-surface-base relative">
              <FlightLogStream
                  onAnswerQuestion={handleAnswerQuestion}
                  onApprove={handleApprove}
                  onReject={handleReject}
                  opening={opening}
                />
              <IgnitionConsole
                onIgnite={handleIgnite}
                onCancel={handleCancel}
                onCancelPending={handleCancelPending}
                loading={opening}
              />

              {/* Mobile Full-Screen Inspector Sheet */}
              {isRightPanelOpen && (
                <div className="fixed inset-0 z-50 bg-void flex flex-col animate-in fade-in duration-200">
                  <div className="h-12 px-4 border-b border-surface-border flex items-center justify-between bg-surface-sidebar">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-semibold text-foreground">Inspector</span>
                      <span className="text-xs text-neutral-500">Changes, files, processes</span>
                    </div>
                    <button
                      type="button"
                      onClick={() => useMissionStore.getState().setIsRightPanelOpen(false)}
                      className="p-1.5 rounded-md hover:bg-surface-elevated text-neutral-400 hover:text-foreground cursor-pointer"
                      aria-label="Close Inspector"
                    >
                      <X className="w-4 h-4" />
                    </button>
                  </div>
                  <div className="flex-1 flex flex-col overflow-hidden">
                    <div className="min-h-0 flex-1 overflow-hidden border-b border-surface-border">
                      <InspectorTop />
                    </div>
                    <div className={`overflow-hidden bg-void ${terminalExpanded ? "h-2/5" : "h-8"}`}>
                      <StreamingTerminal collapsed={!terminalExpanded} onToggle={toggleTerminal} />
                    </div>
                  </div>
                </div>
              )}
            </div>
          ) : (
            <PanelGroup direction="horizontal" className="h-full w-full">
              {/* Center Panel: Continuous Flight Stream & Ignition Console */}
              <Panel
                id="flight-stream-panel"
                order={1}
                defaultSize={isRightPanelOpen ? 50 : 100}
                minSize={30}
                className="flex flex-col h-full bg-surface-base"
              >
                <FlightLogStream
                  onAnswerQuestion={handleAnswerQuestion}
                  onApprove={handleApprove}
                  onReject={handleReject}
                  opening={opening}
                />
                <IgnitionConsole
                onIgnite={handleIgnite}
                onCancel={handleCancel}
                onCancelPending={handleCancelPending}
                loading={opening}
              />
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
                  {/* Top: the inspector tabs; bottom: the terminal, folded to its title until used */}
                  <div className="min-h-0 flex-1 overflow-hidden">
                    <InspectorTop />
                  </div>
                  <div className={`overflow-hidden border-t border-border ${terminalExpanded ? "h-2/5" : "h-8"}`}>
                    <StreamingTerminal collapsed={!terminalExpanded} onToggle={toggleTerminal} />
                  </div>
                </Panel>
              )}
            </PanelGroup>
          )}
        </div>
      </div>

      {/* Modal Settings fallback */}

      {/* Turn Inspector slide-over (persisted turn traces + interrupted-turn retry) */}
      <TurnInspector onRetryTurn={handleRetryTurn} />

      {/* Memory Inspector slide-over (view/forget long-term memory) */}
      <MemoryInspector />

      {/* Artifacts Drawer slide-over (persistent deliverables) */}
      <ArtifactsDrawer />
    </div>
  );
};
