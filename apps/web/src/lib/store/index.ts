import { create } from "zustand";
import { createAgentsSlice } from "./agents";
import { createComposerSlice } from "./composer";
import { createLayoutSlice } from "./layout";
import { createSessionsSlice } from "./sessions";
import { createStreamSlice } from "./stream";
import type { MissionStore } from "./types";

export { DEFAULT_AGENTS } from "./helpers";
export type { IncomingUserMessage, MissionStore, OwnUserMessage } from "./types";

/** The cockpit's state, assembled from slices that each own one concern. */
export const useMissionStore = create<MissionStore>()((...args) => ({
  ...createSessionsSlice(...args),
  ...createAgentsSlice(...args),
  ...createStreamSlice(...args),
  ...createComposerSlice(...args),
  ...createLayoutSlice(...args),
}));
