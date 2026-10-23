import type { StateCreator } from "zustand";
import type { MissionStore, LayoutSlice, InspectorPreference } from "./types";

const PREFERENCE_KEY = "rocket.inspector.preference";

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
  try {
    window.localStorage.setItem(PREFERENCE_KEY, preference);
  } catch {
    // Not remembered; the in-memory choice still holds.
  }
}

export const createLayoutSlice: StateCreator<MissionStore, [], [], LayoutSlice> = (set, get) => ({
  isLeftSidebarOpen: true,
  isSettingsNavOpen: false,
  isRightPanelOpen: false,
  isTurnInspectorOpen: false,
  isMemoryInspectorOpen: false,
  isArtifactsDrawerOpen: false,
  inspectorTab: "changes",
  setInspectorTab: (inspectorTab) => set({ inspectorTab }),
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
  setIsLeftSidebarOpen: (isLeftSidebarOpen) => set({ isLeftSidebarOpen }),
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
  setIsArtifactsDrawerOpen: (isArtifactsDrawerOpen) => set({ isArtifactsDrawerOpen }),
});
