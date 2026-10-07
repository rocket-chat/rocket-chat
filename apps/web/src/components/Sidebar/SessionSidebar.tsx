"use client";

import React, { useState, useRef, useEffect } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMissionStore } from "../../lib/store";
import { AgentPersonaConfig, SessionData } from "../../types/mission";
import { RocketChatIcon } from "../RocketChatLogo";
import { ThemeToggle } from "../Theme/ThemeToggle";
import {
  Trash2,
  Settings,
  ChevronDown,
  Check,
  PanelLeftClose,
  Edit,
} from "lucide-react";

interface SessionSidebarProps {
  onSelectSession?: (session: SessionData) => void;
}

export const SessionSidebar: React.FC<SessionSidebarProps> = ({ onSelectSession }) => {
  const {
    session,
    sessions,
    createSession,
    deleteSession,
    resetAllSessions,
    activeAgent,
    selectAgent,
    availableAgents,
    isLeftSidebarOpen,
    setIsLeftSidebarOpen,
  } = useMissionStore();

  const router = useRouter();
  const [isAgentMenuOpen, setIsAgentMenuOpen] = useState(false);
  const [isCreatingSession, setIsCreatingSession] = useState(false);
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

  const handleCreateNewSession = async () => {
    setIsCreatingSession(true);
    try {
      const newSession = await createSession();
      if (newSession) {
        if (onSelectSession) {
          onSelectSession(newSession);
        }
        router.push(`/chat/${newSession.session_id}`);
      }
    } finally {
      setIsCreatingSession(false);
    }
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

  if (!isLeftSidebarOpen) {
    return null;
  }

  return (
    <aside
      data-testid="session-sidebar"
      className="w-64 flex-shrink-0 bg-surface-sidebar border-r border-surface-border flex flex-col justify-between h-full select-none z-30 transition-all duration-200"
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
            disabled={isCreatingSession}
            className="w-full flex items-center justify-between px-3 py-2 text-xs font-medium text-foreground bg-surface-card hover:bg-surface-elevated border border-surface-border hover:border-neutral-400 dark:hover:border-neutral-600 rounded-lg transition-all group cursor-pointer disabled:opacity-50"
          >
            <span className="flex items-center gap-2">
              <Edit className="w-3.5 h-3.5 text-brand" />
              <span>{isCreatingSession ? "Creating..." : "New conversation"}</span>
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

            <nav className="space-y-0.5">
              {sessions.map((s) => {
                const isSelected = session?.session_id === s.session_id;
                return (
                  <Link
                    key={s.session_id}
                    href={`/chat/${s.session_id}`}
                    onClick={() => {
                      if (onSelectSession) onSelectSession(s);
                    }}
                    className={`group flex items-center justify-between px-2.5 py-1.5 text-xs rounded-md transition-colors ${
                      isSelected
                        ? "bg-surface-elevated text-foreground font-semibold border-l-2 border-brand"
                        : "text-neutral-600 hover:text-foreground dark:text-neutral-400 dark:hover:text-neutral-200 hover:bg-surface-card"
                    }`}
                  >
                    <span className="truncate flex-1 mr-1">
                      {s.title || `Chat ${s.session_id.slice(0, 8)}`}
                    </span>
                    <button
                      type="button"
                      onClick={(e) => handleDeleteSession(e, s.session_id)}
                      className="opacity-0 group-hover:opacity-100 text-neutral-400 hover:text-rose-400 transition-opacity p-0.5"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </Link>
                );
              })}
            </nav>
          </div>
        </div>

        {/* Subnav Footer Info & Settings Link */}
        <div className="p-3 border-t border-surface-border bg-surface-sidebar/50 flex items-center justify-between">
          <Link
            href="/settings/github"
            data-testid="sidebar-settings-link"
            className="flex items-center gap-2 text-xs font-mono text-neutral-600 hover:text-foreground dark:text-neutral-400 dark:hover:text-white transition-colors"
          >
            <Settings className="w-4 h-4 text-brand" />
            <span>Settings</span>
          </Link>
          <ThemeToggle />
        </div>
      </div>
    </aside>
  );
};
