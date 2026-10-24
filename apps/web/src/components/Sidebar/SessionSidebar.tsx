"use client";

import { useShallow } from "zustand/react/shallow";
import React, { useCallback, useState, useEffect } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMissionStore } from "../../lib/store";
import { SessionData } from "../../types/mission";
import { RocketChatIcon } from "../RocketChatLogo";
import { ThemeToggle } from "../Theme/ThemeToggle";
import { IconButton } from "../ui/IconButton";
import { useSession } from "../AuthProvider";
import {
  Trash2,
  Settings,
  PanelLeftClose,
  Edit,
  MessageSquare,
  Pin,
  PinOff,
  Archive,
  ArchiveRestore,
  GitFork,
  CalendarClock,
  Brain,
  History,
  Users,
  Loader2,
  Search,
  PanelLeftOpen,
  Plus,
  CircleAlert,
  Copy,
  Share2,
  Slack,
} from "lucide-react";
import { ago, groupSessions, STATUS_LABEL, statusOf, type SessionStatus } from "../../lib/session-groups";

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
    isLeftSidebarOpen,
    setIsLeftSidebarOpen,
    setSessionFlags,
    fetchSessions,
    loadMoreSessions,
    sessionsNextCursor,
    sessionsScope,
    isLoadingSessions,
    setIsMemoryInspectorOpen,
    setIsTurnInspectorOpen,
  } = useMissionStore(
    useShallow((state) => ({ session: state.session, sessions: state.sessions, initDraftSession: state.initDraftSession, deleteSession: state.deleteSession, resetAllSessions: state.resetAllSessions, isLeftSidebarOpen: state.isLeftSidebarOpen, setIsLeftSidebarOpen: state.setIsLeftSidebarOpen, setSessionFlags: state.setSessionFlags, fetchSessions: state.fetchSessions, loadMoreSessions: state.loadMoreSessions, sessionsNextCursor: state.sessionsNextCursor, sessionsScope: state.sessionsScope, isLoadingSessions: state.isLoadingSessions, setIsMemoryInspectorOpen: state.setIsMemoryInspectorOpen, setIsTurnInspectorOpen: state.setIsTurnInspectorOpen }))
  );

  const router = useRouter();
  const [showArchived, setShowArchived] = useState(false);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<"all" | "shared" | "slack">("all");
  const [menu, setMenu] = useState<{ id: string; x: number; y: number } | null>(null);
  const { data: authSession } = useSession();
  const roles = (authSession?.user as unknown as { roles?: string[] })?.roles || [];
  const isAdmin = !authSession || roles.includes("admin") || roles.includes("owner");
  const showingOrganization = sessionsScope === "organization";

  const handleCreateNewSession = useCallback(() => {
    if (typeof window !== "undefined" && window.innerWidth < 768) {
      setIsLeftSidebarOpen(false);
    }
    const draftSession = initDraftSession();
    if (onSelectSession) {
      onSelectSession(draftSession);
    }
    router.push(`/chat/draft`);
  }, [setIsLeftSidebarOpen, initDraftSession, onSelectSession, router]);

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
  }, [handleCreateNewSession]);

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
      await fetchSessions();
    }
  };

  const handleToggleArchivedView = async () => {
    const next = !showArchived;
    setShowArchived(next);
    await fetchSessions(next);
  };

  const handleToggleOrganizationView = async () => {
    await fetchSessions(showArchived, showingOrganization ? "mine" : "organization");
  };

  const handleClearOwnSessions = async () => {
    const question =
      "Delete all of your sessions, archived ones included, and their sandboxes? " +
      "Sessions of other members and sessions shared with you are kept.";
    if (!confirm(question)) return;
    const fresh = await resetAllSessions();
    if (fresh) {
      router.push(`/chat/${fresh.session_id}`);
    } else {
      alert("Your sessions could not be cleared. Try again.");
    }
  };

  const isSlackSession = (s: SessionData) =>
    s.source === "slack" || s.session_id.startsWith("slack_") || Boolean(s.metadata?.slack_channel_id);

  const visibleSessions = sessions
    .filter((s) => !s.is_draft && s.session_id !== "draft" && s.session_id !== "new")
    // Archived sessions are listed only in their own view, and leave the default one the moment
    // they are archived instead of waiting for the next fetch.
    .filter((s) => Boolean(s.is_archived) === showArchived)
    .filter((s) => (filter === "shared" ? Boolean(s.is_shared) : filter === "slack" ? isSlackSession(s) : true))
    .filter((s) => {
      const words = query.toLowerCase().split(/\s+/).filter(Boolean);
      const haystack = `${s.title ?? ""} ${s.git_repo ?? ""}`.toLowerCase();
      return words.every((word) => haystack.includes(word));
    });
  const groups = groupSessions(visibleSessions);
  const needingYou = sessions.filter((s) => statusOf(s) === "needs_you").length;

  // Closed, the sidebar is a rail on wide screens (new conversation, what needs you, places) and
  // nothing on a phone, where the header opens it as a drawer.
  if (!isLeftSidebarOpen) {
    return (
      <aside
        data-testid="session-rail"
        aria-label="Conversations"
        className="hidden h-full w-14 shrink-0 flex-col items-center gap-2 border-r border-surface-border bg-surface-sidebar py-3 md:flex"
      >
        <IconButton label="Open conversations" onClick={() => setIsLeftSidebarOpen(true)}>
          <PanelLeftOpen className="h-4 w-4" />
        </IconButton>
        <IconButton label="New conversation" onClick={handleCreateNewSession}>
          <Plus className="h-4 w-4 text-brand" />
        </IconButton>
        <button
          type="button"
          onClick={() => setIsLeftSidebarOpen(true)}
          title={needingYou ? `${needingYou} conversation${needingYou === 1 ? "" : "s"} need you` : "Search conversations"}
          aria-label={needingYou ? `${needingYou} conversations need you` : "Search conversations"}
          className="relative rounded-lg p-2 text-neutral-600 hover:bg-surface-elevated hover:text-foreground dark:text-neutral-300"
        >
          {needingYou ? <CircleAlert className="h-4 w-4 text-amber-500" /> : <Search className="h-4 w-4" />}
          {needingYou > 0 && (
            <span className="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-amber-500 px-1 text-[10px] font-semibold text-white">
              {needingYou}
            </span>
          )}
        </button>
        <div className="mt-auto flex flex-col items-center gap-1">
          <Link href="/missions" aria-label="Missions" title="Missions" className="rounded-lg p-2 text-neutral-600 hover:bg-surface-elevated hover:text-foreground dark:text-neutral-300">
            <CalendarClock className="h-4 w-4 text-brand" />
          </Link>
          <Link href="/settings/github" aria-label="Settings" title="Settings" className="rounded-lg p-2 text-neutral-600 hover:bg-surface-elevated hover:text-foreground dark:text-neutral-300">
            <Settings className="h-4 w-4 text-brand" />
          </Link>
        </div>
      </aside>
    );
  }

  const chip = (active: boolean) =>
    `rounded-full border px-2.5 py-1 text-[11px] cursor-pointer transition-colors ${
      active
        ? "border-brand/50 bg-brand/10 text-brand"
        : "border-surface-border text-neutral-600 hover:text-foreground dark:text-neutral-300"
    }`;

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
          <div className="flex items-center justify-between border-b border-surface-borderSubtle px-3 py-2.5">
            <Link href="/" className="group flex items-center gap-2">
              {/* Bare artwork, no frame around it; a hairline appears on hover. */}
              <RocketChatIcon
                size={32}
                className="h-8 w-8 rounded-[22%] ring-1 ring-transparent transition-shadow duration-200 group-hover:ring-brand/50"
              />
              <span className="font-display font-semibold text-sm tracking-tight text-foreground">Rocket Chat</span>
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
          <div className="px-3 pt-3 pb-2 space-y-2">
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
              <kbd className="text-[10px] font-mono text-neutral-500 dark:text-neutral-400 bg-surface-base px-1.5 py-0.5 rounded border border-surface-border">⌘K</kbd>
            </button>

            <div className="relative">
              <Search className="pointer-events-none absolute left-2.5 top-2 h-3.5 w-3.5 text-neutral-500" aria-hidden="true" />
              <input
                type="search"
                data-testid="session-search"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Search conversations"
                aria-label="Search conversations"
                className="w-full rounded-lg border border-surface-border bg-surface-card py-1.5 pl-8 pr-2 text-xs text-foreground placeholder:text-neutral-500 focus:border-brand/60 focus:outline-none"
              />
            </div>

            <div className="flex items-center gap-1.5" role="group" aria-label="Filter conversations">
              <button type="button" aria-pressed={filter === "all"} onClick={() => setFilter("all")} className={chip(filter === "all")}>All</button>
              <IconButton
                label="Shared"
                title="Shared: conversations shared with you, or that you shared"
                pressed={filter === "shared"}
                onClick={() => setFilter(filter === "shared" ? "all" : "shared")}
                className="h-7 w-7"
              >
                <Share2 className="h-3.5 w-3.5" />
              </IconButton>
              <IconButton
                label="Slack"
                title="Slack: conversations started from Slack"
                pressed={filter === "slack"}
                onClick={() => setFilter(filter === "slack" ? "all" : "slack")}
                className="h-7 w-7"
              >
                <Slack className="h-3.5 w-3.5" />
              </IconButton>
              <IconButton
                label="Archived"
                title={showArchived ? "Archived: back to your active conversations" : "Archived: show only archived conversations"}
                data-testid="toggle-archived-btn"
                pressed={showArchived}
                onClick={handleToggleArchivedView}
                className="h-7 w-7"
              >
                <Archive className="h-3.5 w-3.5" />
              </IconButton>
              {isAdmin && (
                <IconButton
                  label="Everyone"
                  title={showingOrganization ? "Everyone: back to your sessions and those shared with you" : "Everyone: show every session in the organization"}
                  data-testid="toggle-org-sessions-btn"
                  pressed={showingOrganization}
                  onClick={handleToggleOrganizationView}
                  className="h-7 w-7"
                >
                  <Users className="h-3.5 w-3.5" />
                </IconButton>
              )}
            </div>
          </div>

          {/* Conversations, grouped by what needs you and by when they last moved */}
          <div className="flex-1 overflow-y-auto px-2 py-2">
            {isLoadingSessions && groups.length === 0 ? (
              <div className="space-y-2 px-2 py-4" data-testid="sessions-loading-skeleton">
                {[1, 2, 3, 4, 5].map((i) => (
                  <div key={i} className="flex items-center gap-2.5 rounded-lg px-2 py-2">
                    <div className="h-4 w-4 animate-pulse rounded bg-neutral-200 dark:bg-neutral-800" />
                    <div className="flex-1 space-y-1.5">
                      <div className="h-3.5 w-3/4 animate-pulse rounded bg-neutral-200 dark:bg-neutral-800" />
                      <div className="h-2.5 w-1/3 animate-pulse rounded bg-neutral-200/60 dark:bg-neutral-800/60" />
                    </div>
                  </div>
                ))}
              </div>
            ) : groups.length === 0 ? (
              <p className="px-3 py-6 text-center text-xs text-neutral-500">
                {showArchived ? "No archived conversations." : query || filter !== "all" ? "No conversation matches." : "No conversations yet. Start one above."}
              </p>
            ) : null}
            {groups.map((group) => (
              <section key={group.name} aria-label={group.name} className="mb-3">
                <h3 className={`px-2 pb-1 text-[11px] font-semibold ${group.name === "Needs you" ? "text-amber-600 dark:text-amber-400" : "text-neutral-500 dark:text-neutral-400"}`}>
                  {group.name}
                  <span className="ml-1 font-normal">({group.sessions.length})</span>
                </h3>
                <nav className="space-y-0.5" aria-label={group.name}>
                  {group.sessions.map((s) => (
                    <SessionRow
                      key={s.session_id}
                      session={s}
                      selected={session?.session_id === s.session_id}
                      showOwner={showingOrganization}
                      isSlack={isSlackSession(s)}
                      onOpen={() => {
                        if (typeof window !== "undefined" && window.innerWidth < 768) setIsLeftSidebarOpen(false);
                        if (onSelectSession) onSelectSession(s);
                      }}
                      onFlag={handleToggleFlag}
                      onDelete={handleDeleteSession}
                      onMenu={(x, y) => setMenu({ id: s.session_id, x, y })}
                    />
                  ))}
                </nav>
              </section>
            ))}

            {sessionsNextCursor && (
              <button
                type="button"
                data-testid="load-more-sessions-btn"
                disabled={isLoadingSessions}
                onClick={() => void loadMoreSessions()}
                className="w-full mt-1 px-2.5 py-1.5 text-[11px] text-neutral-600 hover:text-foreground border border-dashed border-surface-border rounded-md transition-colors cursor-pointer disabled:opacity-50 dark:text-neutral-300"
              >
                {isLoadingSessions ? "Loading…" : "Load more"}
              </button>
            )}

            {sessions.length > 0 && (
              <button
                type="button"
                data-testid="clear-all-sessions-btn"
                title="Delete all of your own sessions"
                onClick={handleClearOwnSessions}
                className="mt-3 w-full text-center text-[11px] text-neutral-500 hover:text-rose-500"
              >
                Clear my conversations
              </button>
            )}
          </div>

          {/* Navigation footer: the places outside the conversation */}
          <div className="space-y-1 border-t border-surface-border bg-surface-sidebar/50 p-2">
            <div className="grid grid-cols-2 gap-1">
              <Link
                href="/missions"
                data-testid="sidebar-missions-link"
                className="flex items-center gap-2 rounded-lg px-2.5 py-2 text-sm text-neutral-700 transition-colors hover:bg-surface-elevated hover:text-foreground dark:text-neutral-300"
              >
                <CalendarClock className="h-4 w-4 text-brand" aria-hidden="true" />
                <span>Missions</span>
              </Link>
              <Link
                href="/settings/github"
                data-testid="sidebar-settings-link"
                className="flex items-center gap-2 rounded-lg px-2.5 py-2 text-sm text-neutral-700 transition-colors hover:bg-surface-elevated hover:text-foreground dark:text-neutral-300"
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

      {menu && (
        <SessionContextMenu
          x={menu.x}
          y={menu.y}
          session={sessions.find((s) => s.session_id === menu.id)}
          onClose={() => setMenu(null)}
          onFlag={handleToggleFlag}
          onDelete={handleDeleteSession}
        />
      )}
    </>
  );
};

const DOT: Record<SessionStatus, string> = {
  needs_you: "bg-amber-500 animate-pulse",
  working: "",
  waiting: "bg-sky-500",
  failed: "bg-rose-500",
  idle: "",
};

/** Where a conversation stands: a spinner while it works, a coloured dot when it needs a look. */
const StatusDot: React.FC<{ status: SessionStatus }> = ({ status }) => {
  if (status === "idle") return null;
  const label = STATUS_LABEL[status];
  return (
    <span data-testid="session-status" data-status={status} title={label} className="mr-1.5 flex shrink-0 items-center">
      {status === "working" ? <Loader2 className="h-3 w-3 animate-spin text-brand" aria-hidden="true" /> : <span className={`h-2 w-2 rounded-full ${DOT[status]}`} aria-hidden="true" />}
      <span className="sr-only">{label}</span>
    </span>
  );
};

interface SessionRowProps {
  session: SessionData;
  selected: boolean;
  showOwner: boolean;
  isSlack: boolean;
  onOpen: () => void;
  onFlag: (e: React.MouseEvent, sessionId: string, flags: { is_pinned?: boolean; is_archived?: boolean }) => void;
  onDelete: (e: React.MouseEvent, sessionId: string) => void;
  onMenu: (x: number, y: number) => void;
}

const SessionRow: React.FC<SessionRowProps> = ({ session: s, selected, showOwner, isSlack, onOpen, onFlag, onDelete, onMenu }) => {
  const fallbackTitle = s.session_id.startsWith("session_") ? `Chat ${s.session_id.slice(8, 16)}` : `Chat ${s.session_id.slice(0, 8)}`;
  const slackChannel = s.metadata?.slack_channel_id as string | undefined;
  const slackThreadTs = s.metadata?.slack_thread_ts as string | undefined;
  const slackDeepLink = slackChannel
    ? `https://slack.com/app_redirect?channel=${slackChannel}${slackThreadTs ? `&message_ts=${slackThreadTs}` : ""}`
    : null;
  const forkedFrom = s.forked_from ?? (s.metadata?.forked_from as { session_id?: string } | undefined);
  const repo = (s.git_repo ?? "").split("/").pop();
  const detail = [repo, ago(s)].filter(Boolean).join(" · ");
  const title = s.title || fallbackTitle;

  return (
    <Link
      href={`/chat/${s.session_id}`}
      data-testid="session-row"
      data-session-id={s.session_id}
      data-pinned={s.is_pinned ? "true" : "false"}
      data-archived={s.is_archived ? "true" : "false"}
      aria-current={selected ? "page" : undefined}
      title={`${title}${detail ? `\n${detail}` : ""}${showOwner ? `\nOwner: ${s.tenant_user_id}` : ""}`}
      onClick={onOpen}
      onContextMenu={(e) => {
        e.preventDefault();
        onMenu(e.clientX, e.clientY);
      }}
      className={`group flex items-center justify-between rounded-md px-2.5 py-1.5 text-xs transition-colors ${
        selected
          ? "border-l-2 border-brand bg-surface-elevated font-semibold text-foreground"
          : "text-neutral-700 hover:bg-surface-card hover:text-foreground dark:text-neutral-300 dark:hover:text-neutral-100"
      }`}
    >
      <div className="mr-1 flex min-w-0 flex-1 items-center">
        <StatusDot status={statusOf(s)} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center">
            {s.is_pinned && (
              <span data-testid="session-pinned-indicator" title="Pinned" className="mr-1 shrink-0 text-brand">
                <Pin className="h-3 w-3" />
              </span>
            )}
            {isSlack && (
              <span className="mr-1.5 flex shrink-0 items-center gap-1" title="Slack Thread">
                {slackDeepLink ? (
                  <a
                    href={slackDeepLink}
                    target="_blank"
                    rel="noopener noreferrer"
                    title="Open in Slack"
                    onClick={(e) => e.stopPropagation()}
                    className="p-0.5 text-amber-500 transition-colors hover:text-rose-500"
                  >
                    <MessageSquare className="h-3.5 w-3.5" />
                  </a>
                ) : (
                  <MessageSquare className="h-3.5 w-3.5 text-amber-500" />
                )}
              </span>
            )}
            <span className="flex-1 truncate">{title}</span>
            {forkedFrom?.session_id && (
              <span data-testid="session-fork-indicator" title={`Forked from ${forkedFrom.session_id}`} className="ml-1 shrink-0 text-neutral-500">
                <GitFork className="h-3 w-3" />
              </span>
            )}
            {s.is_archived && (
              <span data-testid="session-archived-indicator" title="Archived" className="ml-1 shrink-0 text-[9px] text-neutral-500">
                ARCH
              </span>
            )}
          </div>
          {detail && <div className="truncate text-[10px] font-normal text-neutral-500 dark:text-neutral-400">{detail}</div>}
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity focus-within:opacity-100 group-hover:opacity-100">
        <button
          type="button"
          data-testid="pin-session-btn"
          aria-label={s.is_pinned ? "Unpin session" : "Pin session"}
          title={s.is_pinned ? "Unpin session" : "Pin session"}
          onClick={(e) => onFlag(e, s.session_id, { is_pinned: !s.is_pinned })}
          className="p-0.5 text-neutral-500 hover:text-brand"
        >
          {s.is_pinned ? <PinOff className="h-3.5 w-3.5" /> : <Pin className="h-3.5 w-3.5" />}
        </button>
        <button
          type="button"
          data-testid="archive-session-btn"
          aria-label={s.is_archived ? "Unarchive session" : "Archive session"}
          title={s.is_archived ? "Unarchive session" : "Archive session"}
          onClick={(e) => onFlag(e, s.session_id, { is_archived: !s.is_archived })}
          className="p-0.5 text-neutral-500 hover:text-amber-500"
        >
          {s.is_archived ? <ArchiveRestore className="h-3.5 w-3.5" /> : <Archive className="h-3.5 w-3.5" />}
        </button>
        <button
          type="button"
          data-testid="delete-session-btn"
          aria-label="Delete session"
          title="Delete session"
          onClick={(e) => onDelete(e, s.session_id)}
          className="p-0.5 text-neutral-500 hover:text-rose-500"
        >
          <Trash2 className="h-3.5 w-3.5" />
        </button>
      </div>
    </Link>
  );
};

/** Right-click menu of one conversation: the row's actions plus copying its link. */
const SessionContextMenu: React.FC<{
  x: number;
  y: number;
  session?: SessionData;
  onClose: () => void;
  onFlag: SessionRowProps["onFlag"];
  onDelete: SessionRowProps["onDelete"];
}> = ({ x, y, session, onClose, onFlag, onDelete }) => {
  useEffect(() => {
    const close = () => onClose();
    const escape = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("click", close);
    window.addEventListener("scroll", close, true);
    window.addEventListener("keydown", escape);
    return () => {
      window.removeEventListener("click", close);
      window.removeEventListener("scroll", close, true);
      window.removeEventListener("keydown", escape);
    };
  }, [onClose]);
  if (!session) return null;
  const item = "flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs text-foreground hover:bg-surface-elevated";
  return (
    <ul
      role="menu"
      data-testid="session-context-menu"
      style={{ left: Math.min(x, window.innerWidth - 200), top: Math.min(y, window.innerHeight - 180) }}
      className="fixed z-[70] w-48 overflow-hidden rounded-lg border border-surface-border bg-surface-base py-1 shadow-xl"
    >
      <li role="none">
        <button role="menuitem" type="button" className={item} onClick={(e) => onFlag(e, session.session_id, { is_pinned: !session.is_pinned })}>
          <Pin className="h-3.5 w-3.5" /> {session.is_pinned ? "Unpin" : "Pin"}
        </button>
      </li>
      <li role="none">
        <button role="menuitem" type="button" className={item} onClick={(e) => onFlag(e, session.session_id, { is_archived: !session.is_archived })}>
          <Archive className="h-3.5 w-3.5" /> {session.is_archived ? "Unarchive" : "Archive"}
        </button>
      </li>
      <li role="none">
        <button
          role="menuitem"
          type="button"
          className={item}
          onClick={() => void navigator.clipboard?.writeText(`${window.location.origin}/chat/${session.session_id}`)}
        >
          <Copy className="h-3.5 w-3.5" /> Copy link
        </button>
      </li>
      <li role="none">
        <button role="menuitem" type="button" className={`${item} text-rose-600 dark:text-rose-400`} onClick={(e) => onDelete(e, session.session_id)}>
          <Trash2 className="h-3.5 w-3.5" /> Delete
        </button>
      </li>
    </ul>
  );
};
