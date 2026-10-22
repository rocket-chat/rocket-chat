export interface UploadedAttachment {
  attachment_id: string;
  name: string;
  mime: string;
  size: number;
  sha256: string;
  workspace_path: string;
  preview?: string | null;
}

/** Upload one file to an existing session; rejects with the server's message on failure. */
export async function postAttachment(sessionId: string, file: File): Promise<UploadedAttachment> {
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
  return res.json();
}
