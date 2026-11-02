import type { StateCreator } from "zustand";
import type { MissionStore, SettingsSlice } from "./types";

export const createSettingsSlice: StateCreator<MissionStore, [], [], SettingsSlice> = (set) => ({
  settingsOwner: "all",
  setSettingsOwner: (settingsOwner) => set({ settingsOwner }),
});
