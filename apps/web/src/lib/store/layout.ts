import type { StateCreator } from "zustand";
import type { MissionStore, LayoutSlice } from "./types";

export const createLayoutSlice: StateCreator<MissionStore, [], [], LayoutSlice> = (set) => ({
  isLeftSidebarOpen: true,
  isSettingsNavOpen: false,
  isRightPanelOpen: false,
  isTurnInspectorOpen: false,
  isMemoryInspectorOpen: false,
  setIsLeftSidebarOpen: (isLeftSidebarOpen) => set({ isLeftSidebarOpen }),
  setIsSettingsNavOpen: (isSettingsNavOpen) => set({ isSettingsNavOpen }),
  setIsRightPanelOpen: (isRightPanelOpen) => set({ isRightPanelOpen }),
  setIsTurnInspectorOpen: (isTurnInspectorOpen) => set({ isTurnInspectorOpen }),
  setIsMemoryInspectorOpen: (isMemoryInspectorOpen) => set({ isMemoryInspectorOpen }),
});
