"use client";

import React, { useState, useRef, useEffect } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMissionStore } from "../../lib/store";
import { AgentPersonaConfig, SessionData } from "../../types/mission";
import { RocketChatIcon } from "../RocketChatLogo";
import { ThemeToggle } from "../Theme/ThemeToggle";
import { IconButton } from "../ui/IconButton";
import {
  Trash2,
  Settings,
  ChevronDown,
  Check,
  PanelLeftClose,
  Edit,
  MessageSquare,
  Pin,
  PinOff,
  Archive,
  ArchiveRestore,
  GitFork,
  Eye,
  EyeOff,
  CalendarClock,
  Brain,
  History,
} from "lucide-react";

interface SessionSidebarProps {
  onSelectSession?: (session: SessionData) => void;
}

export const SessionSidebar: React.FC<SessionSidebarProps> = ({ onSelectSession }) => {
  const {
    session,
    sessions,
    initDraftSession,
    deleteSession,
    resetAllSessions,
    activeAgent,
    selectAgent,
    availableAgents,
    isLeftSidebarOpen,
    setIsLeftSidebarOpen,
    setSessionFlags,
    fetchSessions,
    loadMoreSessions,
    sessionsNextCursor,
    isLoadingSessions,
    setIsMemoryInspectorOpen,
    setIsTurnInspectorOpen,
  } = useMissionStore();

  const router = useRouter();
  const [isAgentMenuOpen, setIsAgentMenuOpen] = useState(false);
  const [showArchived, setShowArchived] = useState(false);
  const agentMenuRef = useRef<HTMLDivElement>(null);

  // Close agent dropdown when clicking outside
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (agentMenuRef.current && !agentMenuRef.current.contains(e.target as Node)) {
        setIsAgentMenuOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  // Keyboard shortcut Cmd+K / Ctrl+K for new conversation
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        handleCreateNewSession();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, []);

  const handleCreateNewSession = () => {
    if (typeof window !== "undefined" && window.innerWidth < 768) {
      setIsLeftSidebarOpen(false);
    }
    const draftSession = initDraftSession();
    if (onSelectSession) {
      onSelectSession(draftSession);
    }
    router.push(`/chat/draft`);
  };

  const handleDeleteSession = async (e: React.MouseEvent, sessionId: string) => {
    e.preventDefault();
    e.stopPropagation();
    if (confirm("Delete this session and tear down its sandbox?")) {
      const remaining = sessions.filter((s) => s.session_id !== sessionId);
      await deleteSession(sessionId);
      if (session?.session_id === sessionId) {
        if (remaining.length > 0) {
          router.push(`/chat/${remaining[0].session_id}`);
        } else {
          router.push(`/`);
        }
      }
    }
  };

  const handleToggleFlag = async (
    e: React.MouseEvent,
    sessionId: string,
    flags: { is_pinned?: boolean; is_archived?: boolean }
  ) => {
    e.preventDefault();
    e.stopPropagation();
    const ok = await setSessionFlags(sessionId, flags);
    if (!ok) {
      // The server rejected the mutation (offline, permission, stale session): re-fetch
      // so the sidebar never shows a pin/archive state the backend did not accept.
      await fetchSessions(showArchived);
    }
  };

  const handleToggleArchivedView = async () => {
    const next = !showArchived;
    setShowArchived(next);
    await fetchSessions(next);
  };

  // Pinned sessions float to the top of the list; the API already returns the rest in
  // newest-first order and a stable sort keeps that order inside each group.
  const orderedSessions = [...sessions].sort(
    (a, b) => Number(Boolean(b.is_pinned)) - Number(Boolean(a.is_pinned))
  );

  if (!isLeftSidebarOpen) {
    return null;
  }

  return (
    <>
      {/* Mobile Backdrop */}
      <div
        className="fixed inset-0 bg-black/60 backdrop-blur-sm z-40 md:hidden"
        onClick={() => setIsLeftSidebarOpen(false)}
        aria-hidden="true"
      />

      <aside
        data-testid="session-sidebar"
        className="fixed inset-y-0 left-0 z-50 w-72 max-w-[85vw] md:relative md:w-64 md:inset-auto md:z-30 flex-shrink-0 bg-surface-sidebar border-r border-surface-border flex flex-col justify-between h-full select-none shadow-2xl md:shadow-none transition-all duration-200"
      >
      <div className="flex flex-col h-full overflow-hidden">
        {/* Brand & Toggle Header */}
        <div className="p-3.5 flex items-center justify-between border-b border-surface-borderSubtle">
          <Link href="/" className="flex items-center gap-2.5 group">
            <div className="w-7 h-7 rounded-md bg-brand/10 border border-brand/30 flex items-center justify-center text-brand transition-colors group-hover:bg-brand group-hover:text-white">
              <RocketChatIcon className="w-4 h-4" />
            </div>
            <div className="flex items-center gap-1.5">
              <span className="font-display font-semibold text-sm tracking-tight text-foreground">
                Rocket Chat
              </span>
            </div>
          </Link>

          <button
            type="button"
            onClick={() => setIsLeftSidebarOpen(false)}
            aria-label="Collapse sidebar"
            className="p-1.5 text-neutral-500 hover:text-foreground dark:text-neutral-400 dark:hover:text-white hover:bg-surface-elevated rounded-md transition-colors cursor-pointer"
            title="Collapse Sidebar"
          >
            <PanelLeftClose className="w-4 h-4" />
          </button>
        </div>

        {/* Quick Action: New Thread (with ⌘K indicator) */}
        <div className="px-3 pt-3 pb-2">
          <button
            type="button"
            data-testid="new-conversation-btn"
            onClick={handleCreateNewSession}
            className="w-full flex items-center justify-between px-3 py-2 text-xs font-medium text-foreground bg-surface-card hover:bg-surface-elevated border border-surface-border hover:border-neutral-400 dark:hover:border-neutral-600 rounded-lg transition-all group cursor-pointer"
          >
            <span className="flex items-center gap-2">
              <Edit className="w-3.5 h-3.5 text-brand" />
              <span>New conversation</span>
            </span>
            <kbd className="text-[10px] font-mono text-neutral-500 dark:text-neutral-400 bg-surface-base px-1.5 py-0.5 rounded border border-surface-border">
              ⌘K
            </kbd>
          </button>
        </div>

        {/* Navigation & History List */}
        <div className="flex-1 overflow-y-auto px-2 py-2 space-y-4">
          {/* Active / Assigned Persona Header */}
          <div className="px-2" ref={agentMenuRef}>
            <div className="flex items-center justify-between pb-1.5 text-[11px] font-mono font-medium tracking-wider uppercase text-neutral-500 dark:text-neutral-400">
              <span>SPECIALIZED AGENT</span>
              <span className="text-brand font-mono text-[10px]">ACTIVE</span>
            </div>

            <div className="relative">
              <button
                type="button"
                data-testid="assigned-agent-button"
                onClick={() => setIsAgentMenuOpen(!isAgentMenuOpen)}
                className="w-full flex items-center justify-between p-2 rounded-lg border border-surface-border bg-surface-card hover:bg-surface-elevated text-left transition-colors"
              >
                <div className="truncate">
                  <div className="text-xs font-semibold text-foreground truncate">
                    {activeAgent?.name || "Full-Stack Engineer"}
                  </div>
                  <div className="text-[10px] font-mono text-neutral-500 dark:text-neutral-400 truncate">
                    {activeAgent?.role_title || "Senior Systems Engineer"}
                  </div>
                </div>
                <ChevronDown className="w-3.5 h-3.5 text-neutral-400 shrink-0 ml-1" />
              </button>

              {isAgentMenuOpen && (
                <div className="absolute top-full left-0 w-60 mt-1 rounded-xl border border-surface-border bg-surface-card shadow-2xl z-50 py-1 max-h-64 overflow-y-auto">
                  {availableAgents.map((agent: AgentPersonaConfig) => {
                    const isSelected = activeAgent?.id === agent.id;
                    return (
                      <button
                        key={agent.id}
                        type="button"
                        onClick={() => {
                          selectAgent(agent);
                          setIsAgentMenuOpen(false);
                        }}
                        className={`w-full flex items-center justify-between px-3 py-2 text-left text-xs hover:bg-surface-elevated transition-colors ${
                          isSelected ? "bg-surface-elevated text-brand font-semibold" : "text-neutral-700 dark:text-neutral-300"
                        }`}
                      >
                        <span className="truncate">{agent.name}</span>
                        {isSelected && <Check className="w-3.5 h-3.5 text-brand" />}
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          </div>

          {/* Sessions List */}
          <div>
            <div className="px-2 pb-1.5 flex items-center justify-between text-[11px] font-mono font-medium tracking-wider uppercase text-neutral-500 dark:text-neutral-400">
              <span>Sessions ({sessions.length})</span>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  data-testid="toggle-archived-btn"
                  aria-pressed={showArchived}
                  title={showArchived ? "Hide archived sessions" : "Show archived sessions"}
                  onClick={handleToggleArchivedView}
                  className={`flex items-center gap-1 text-[9px] cursor-pointer hover:text-foreground ${
                    showArchived ? "text-brand" : ""
                  }`}
                >
                  {showArchived ? <Eye className="w-3 h-3" /> : <EyeOff className="w-3 h-3" />}
                  ARCHIVED
                </button>
                {sessions.length > 0 && (
                  <button
                    type="button"
                    data-testid="clear-all-sessions-btn"
                    onClick={async () => {
                      if (confirm("Clear all chat sessions?")) {
                        const newS = await resetAllSessions();
                        if (newS) {
                          router.push(`/chat/${newS.session_id}`);
                        }
                      }
                    }}
                    className="hover:text-rose-400 text-[9px] cursor-pointer"
                  >
                    CLEAR
                  </button>
                )}
              </div>
            </div>

            <nav className="space-y-0.5">
              {orderedSessions
                .filter((s) => !s.is_draft && s.session_id !== "draft" && s.session_id !== "new")
                .map((s) => {
                  const isSelected = session?.session_id === s.session_id;
                  const fallbackTitle = s.session_id.startsWith("session_")
                    ? `Chat ${s.session_id.slice(8, 16)}`
                    : `Chat ${s.session_id.slice(0, 8)}`;
                  const isSlack =
                    s.source === "slack" ||
                    s.session_id.startsWith("slack_") ||
                    Boolean(s.metadata?.slack_channel_id);
                  const slackChannel = s.metadata?.slack_channel_id as string | undefined;
                  const slackThreadTs = s.metadata?.slack_thread_ts as string | undefined;
                  const slackDeepLink = slackChannel
                    ? `https://slack.com/app_redirect?channel=${slackChannel}${slackThreadTs ? `&message_ts=${slackThreadTs}` : ""}`
                    : null;
                  const forkedFrom =
                    s.forked_from ??
                    (s.metadata?.forked_from as { session_id?: string } | undefined);

                  return (
                    <Link
                      key={s.session_id}
                      href={`/chat/${s.session_id}`}
                      data-testid="session-row"
                      data-session-id={s.session_id}
                      data-pinned={s.is_pinned ? "true" : "false"}
                      data-archived={s.is_archived ? "true" : "false"}
                      onClick={() => {
                        if (typeof window !== "undefined" && window.innerWidth < 768) {
                          setIsLeftSidebarOpen(false);
                        }
                        if (onSelectSession) onSelectSession(s);
                      }}
                      className={`group flex items-center justify-between px-2.5 py-1.5 text-xs rounded-md transition-colors ${
                        isSelected
                          ? "bg-surface-elevated text-foreground font-semibold border-l-2 border-brand"
                          : "text-neutral-600 hover:text-foreground dark:text-neutral-400 dark:hover:text-neutral-200 hover:bg-surface-card"
                      }`}
                    >
                      <div className="flex items-center min-w-0 flex-1 mr-1">
                        {s.is_pinned && (
                          <span
                            data-testid="session-pinned-indicator"
                            title="Pinned"
                            className="shrink-0 mr-1 text-brand"
                          >
                            <Pin className="w-3 h-3" />
                          </span>
                        )}
                        {isSlack && (
                          <span className="flex items-center gap-1 shrink-0 mr-1.5" title="Slack Thread">
                            {slackDeepLink ? (
                              <a
                                href={slackDeepLink}
                                target="_blank"
                                rel="noopener noreferrer"
                                title="Open in Slack"
                                onClick={(e) => e.stopPropagation()}
                                className="text-amber-500 hover:text-rose-500 transition-colors p-0.5"
                              >
                                <MessageSquare className="w-3.5 h-3.5" />
                              </a>
                            ) : (
                              <MessageSquare className="w-3.5 h-3.5 text-amber-500" />
                            )}
                          </span>
                        )}
                        <span className="truncate flex-1">
                          {s.title || fallbackTitle}
                        </span>
                        {forkedFrom?.session_id && (
                          <span
                            data-testid="session-fork-indicator"
                            title={`Forked from ${forkedFrom.session_id}`}
                            className="shrink-0 ml-1 text-neutral-500"
                          >
                            <GitFork className="w-3 h-3" />
                          </span>
                        )}
                        {s.is_archived && (
                          <span
                            data-testid="session-archived-indicator"
                            title="Archived"
                            className="shrink-0 ml-1 text-[9px] font-mono text-neutral-500"
                          >
                            ARCH
                          </span>
                        )}
                      </div>
                      <div className="flex items-center gap-0.5 shrink-0 opacity-0 group-hover:opacity-100 transition-opacity">
                        <button
                          type="button"
                          data-testid="pin-session-btn"
                          aria-label={s.is_pinned ? "Unpin session" : "Pin session"}
                          title={s.is_pinned ? "Unpin session" : "Pin session"}
                          onClick={(e) =>
                            handleToggleFlag(e, s.session_id, { is_pinned: !s.is_pinned })
                          }
                          className="text-neutral-400 hover:text-brand p-0.5"
                        >
                          {s.is_pinned ? (
                            <PinOff className="w-3.5 h-3.5" />
                          ) : (
                            <Pin className="w-3.5 h-3.5" />
                          )}
                        </button>
                        <button
                          type="button"
                          data-testid="archive-session-btn"
                          aria-label={s.is_archived ? "Unarchive session" : "Archive session"}
                          title={s.is_archived ? "Unarchive session" : "Archive session"}
                          onClick={(e) =>
                            handleToggleFlag(e, s.session_id, { is_archived: !s.is_archived })
                          }
                          className="text-neutral-400 hover:text-amber-400 p-0.5"
                        >
                          {s.is_archived ? (
                            <ArchiveRestore className="w-3.5 h-3.5" />
                          ) : (
                            <Archive className="w-3.5 h-3.5" />
                          )}
                        </button>
                        <button
                          type="button"
                          data-testid="delete-session-btn"
                          aria-label="Delete session"
                          title="Delete session"
                          onClick={(e) => handleDeleteSession(e, s.session_id)}
                          className="text-neutral-400 hover:text-rose-400 p-0.5"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    </Link>
                  );
                })}
            </nav>

            {sessionsNextCursor && (
              <button
                type="button"
                data-testid="load-more-sessions-btn"
                disabled={isLoadingSessions}
                onClick={() => void loadMoreSessions()}
                className="w-full mt-2 px-2.5 py-1.5 text-[10px] font-mono uppercase tracking-wider text-neutral-500 hover:text-foreground border border-dashed border-surface-border rounded-md transition-colors cursor-pointer disabled:opacity-50"
              >
                {isLoadingSessions ? "Loading…" : "Load more"}
              </button>
            )}
            </div>
          </div>

          {/* Navigation footer: the places outside the conversation */}
          <div className="space-y-1 border-t border-surface-border bg-surface-sidebar/50 p-2">
            <div className="grid grid-cols-2 gap-1">
              <Link
                href="/missions"
                data-testid="sidebar-missions-link"
                className="flex items-center gap-2 rounded-lg px-2.5 py-2 text-sm text-neutral-600 transition-colors hover:bg-surface-elevated hover:text-foreground dark:text-neutral-400"
              >
                <CalendarClock className="h-4 w-4 text-brand" aria-hidden="true" />
                <span>Missions</span>
              </Link>
              <Link
                href="/settings/github"
                data-testid="sidebar-settings-link"
                className="flex items-center gap-2 rounded-lg px-2.5 py-2 text-sm text-neutral-600 transition-colors hover:bg-surface-elevated hover:text-foreground dark:text-neutral-400"
              >
                <Settings className="h-4 w-4 text-brand" aria-hidden="true" />
                <span>Settings</span>
              </Link>
            </div>
            {/* The header carries these on wider screens; on a phone they live here. */}
            <div className="flex items-center gap-1 sm:hidden">
              <ThemeToggle />
              <IconButton
                label="Open memory inspector"
                onClick={() => {
                  setIsMemoryInspectorOpen(true);
                  setIsLeftSidebarOpen(false);
                }}
              >
                <Brain className="h-4 w-4" />
              </IconButton>
              <IconButton
                label="Turn Inspector"
                onClick={() => {
                  setIsTurnInspectorOpen(true);
                  setIsLeftSidebarOpen(false);
                }}
              >
                <History className="h-4 w-4" />
              </IconButton>
            </div>
          </div>
        </div>
      </aside>
    </>
  );
};
