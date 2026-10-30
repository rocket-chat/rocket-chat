import type { StateCreator } from "zustand";
import type { MissionStore, LayoutSlice, InspectorPreference, InspectorTab } from "./types";

const PREFERENCE_KEY = "rocket.inspector.preference";
const TAB_KEY = "rocket.inspector.tab";
const SIDEBAR_KEY = "rocket.sidebar.open";
const INSPECTOR_TABS: readonly InspectorTab[] = ["changes", "git", "files", "processes", "subagents", "artifacts", "preview"];

function readPreference(): InspectorPreference {
  try {
    const stored = window.localStorage.getItem(PREFERENCE_KEY);
    return stored === "open" || stored === "closed" ? stored : null;
  } catch {
    // Private windows and blocked site data: the choice then only lasts until a reload.
    return null;
  }
}

function writePreference(preference: Exclude<InspectorPreference, null>): void {
  remember(PREFERENCE_KEY, preference);
}

function remember(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // Not remembered; the in-memory choice still holds.
  }
}

function recall(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

/** A phone opens and closes the sidebar as a drawer: that says nothing about the wide layout. */
const isWide = () => typeof window !== "undefined" && window.innerWidth >= 768;

export const createLayoutSlice: StateCreator<MissionStore, [], [], LayoutSlice> = (set, get) => ({
  isLeftSidebarOpen: true,
  isSettingsNavOpen: false,
  isRightPanelOpen: false,
  isTurnInspectorOpen: false,
  isMemoryInspectorOpen: false,
  inspectorTab: "changes",
  setInspectorTab: (inspectorTab) => {
    remember(TAB_KEY, inspectorTab);
    set({ inspectorTab });
  },
  openInspectorTab: (tab) => {
    get().setInspectorTab(tab);
    get().setIsRightPanelOpen(true);
  },
  // Read after the first render, so the server's markup and the browser's first one agree.
  restoreLayoutPreferences: () => {
    const tab = recall(TAB_KEY);
    const sidebar = recall(SIDEBAR_KEY);
    set({
      ...(tab && (INSPECTOR_TABS as readonly string[]).includes(tab) ? { inspectorTab: tab as InspectorTab } : {}),
      ...(sidebar && isWide() ? { isLeftSidebarOpen: sidebar === "open" } : {}),
    });
  },
  inspectorAutoOpened: false,
  autoOpenInspector: (tab) => {
    const state = get();
    if (state.inspectorAutoOpened || state.isRightPanelOpen) return;
    // Someone who closed the panel did not want it: only a person opens it from then on.
    if (state.inspectorPreference === "closed") return;
    // A phone shows the inspector full-screen: never take the screen from the conversation.
    if (typeof window !== "undefined" && window.innerWidth < 1024) return;
    set({ isRightPanelOpen: true, inspectorTab: tab, inspectorAutoOpened: true });
  },
  terminalOpen: null,
  setTerminalOpen: (terminalOpen) => set({ terminalOpen }),
  terminalActivity: false,
  runningProcesses: 0,
  adjustRunningProcesses: (delta) => set((state) => ({ runningProcesses: Math.max(0, state.runningProcesses + delta) })),
  setIsLeftSidebarOpen: (isLeftSidebarOpen) => {
    if (isWide()) remember(SIDEBAR_KEY, isLeftSidebarOpen ? "open" : "closed");
    set({ isLeftSidebarOpen });
  },
  setIsSettingsNavOpen: (isSettingsNavOpen) => set({ isSettingsNavOpen }),
  // Only people call this (the header, the panel's close button, "Review changes"): the
  // automatic opening above sets the state itself and so never counts as a choice.
  inspectorPreference: typeof window === "undefined" ? null : readPreference(),
  setIsRightPanelOpen: (isRightPanelOpen) => {
    writePreference(isRightPanelOpen ? "open" : "closed");
    set({ isRightPanelOpen, inspectorPreference: isRightPanelOpen ? "open" : "closed" });
  },
  setIsTurnInspectorOpen: (isTurnInspectorOpen) => set({ isTurnInspectorOpen }),
  setIsMemoryInspectorOpen: (isMemoryInspectorOpen) => set({ isMemoryInspectorOpen }),
});
