import type { StateCreator } from "zustand";
import type {
  AttachmentInfo,
} from "../../types/mission";
import type { MissionStore, ComposerSlice } from "./types";

export const createComposerSlice: StateCreator<MissionStore, [], [], ComposerSlice> = (set, get) => ({
  composerSeed: null,
  seedComposer: (text) => set({ composerSeed: { text, nonce: Date.now() } }),
  queuedPrompt: null,
  pendingAttachments: [],
  attachmentError: null,
  suggestedFollowup: null,
  setSuggestedFollowup: (suggestedFollowup) => set({ suggestedFollowup }),
  setQueuedPrompt: (queuedPrompt) => set({ queuedPrompt }),
  popQueuedPrompt: () => {
    const p = get().queuedPrompt;
    if (p) set({ queuedPrompt: null });
    return p;
  },
  uploadAttachment: async (file: File) => {
    const session = get().session;
    const sessionId = session?.session_id;
    if (!sessionId || session.is_draft || sessionId === "draft" || sessionId === "new") {
      // A clear failure path beats silently dropping the file.
      set({ attachmentError: "Send a first message to create the session before attaching files." });
      return;
    }

    const tempId = `pending-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
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
      const form = new FormData();
      form.append("file", file);
      const res = await fetch(`/v1/sessions/${sessionId}/attachments`, {
        method: "POST",
        body: form,
        signal: AbortSignal.timeout(30000),
      });
      if (!res.ok) {
        const detail = await res.text();
        throw new Error(detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
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
