import type { StateCreator } from "zustand";
import type {
  AttachmentInfo,
} from "../../types/mission";
import { postAttachment } from "../attachmentUpload";
import type { MissionStore, ComposerSlice } from "./types";

export const createComposerSlice: StateCreator<MissionStore, [], [], ComposerSlice> = (set, get) => ({
  pendingMessages: [],
  addPendingMessage: (message) =>
    set((state) =>
      state.pendingMessages.some((m) => m.messageId === message.messageId)
        ? state
        : { pendingMessages: [...state.pendingMessages, message] }
    ),
  setPendingMessages: (pendingMessages) => set({ pendingMessages }),
  removePendingMessage: (messageId) =>
    set((state) => ({ pendingMessages: state.pendingMessages.filter((m) => m.messageId !== messageId) })),
  composerSeed: null,
  seedComposer: (text) => set({ composerSeed: { text, nonce: Date.now() } }),
  clearComposerSeed: () => set({ composerSeed: null }),
  pendingAttachments: [],
  attachmentError: null,
  suggestedFollowup: null,
  setSuggestedFollowup: (suggestedFollowup) => set({ suggestedFollowup }),
  uploadAttachment: async (file: File) => {
    const session = get().session;
    const sessionId = session?.session_id;
    const isDraft = !sessionId || session.is_draft || sessionId === "draft" || sessionId === "new";

    const tempId = `pending-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    if (isDraft || !sessionId) {
      // No session exists before the first message: keep the file here, the first send uploads it.
      const staged: AttachmentInfo = {
        attachmentId: tempId,
        name: file.name,
        mime: file.type || "application/octet-stream",
        size: file.size,
        sha256: "",
        workspacePath: "",
        status: "staged",
        file,
      };
      set((state) => ({ pendingAttachments: [...state.pendingAttachments, staged], attachmentError: null }));
      return;
    }

    const staged: AttachmentInfo = {
      attachmentId: tempId,
      name: file.name,
      mime: file.type || "application/octet-stream",
      size: file.size,
      sha256: "",
      workspacePath: "",
      status: "uploading",
    };
    set((state) => ({
      pendingAttachments: [...state.pendingAttachments, staged],
      attachmentError: null,
    }));

    try {
      const data = await postAttachment(sessionId, file);
      set((state) => ({
        pendingAttachments: state.pendingAttachments.map((item) =>
          item.attachmentId === tempId
            ? {
                ...item,
                attachmentId: data.attachment_id,
                name: data.name,
                mime: data.mime,
                size: data.size,
                sha256: data.sha256,
                workspacePath: data.workspace_path,
                preview: data.preview ?? null,
                status: "ready" as const,
              }
            : item
        ),
      }));
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      set((state) => ({
        pendingAttachments: state.pendingAttachments.map((item) =>
          item.attachmentId === tempId ? { ...item, status: "error" as const, error: message } : item
        ),
        attachmentError: message,
      }));
    }
  },
  removeAttachment: (attachmentId) =>
    set((state) => ({
      pendingAttachments: state.pendingAttachments.filter(
        (item) => item.attachmentId !== attachmentId
      ),
    })),
  clearAttachments: () => set({ pendingAttachments: [], attachmentError: null }),
});
