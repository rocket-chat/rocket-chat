import type { StateCreator } from "zustand";
import type { MissionStore, LayoutSlice } from "./types";

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
  setIsRightPanelOpen: (isRightPanelOpen) => set({ isRightPanelOpen }),
  setIsTurnInspectorOpen: (isTurnInspectorOpen) => set({ isTurnInspectorOpen }),
  setIsMemoryInspectorOpen: (isMemoryInspectorOpen) => set({ isMemoryInspectorOpen }),
  setIsArtifactsDrawerOpen: (isArtifactsDrawerOpen) => set({ isArtifactsDrawerOpen }),
});
