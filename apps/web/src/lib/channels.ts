import { slackThreadLink } from "./slack";

export type ThreadPlatform = "slack" | "teams" | "mattermost" | "github";

/** The conversation outside Rocket Chat a session is bound to (a Slack, Teams or Mattermost
 * thread, a GitHub issue or pull request), with a link to open it when one is known. */
export interface ThreadBinding {
  platform: ThreadPlatform;
  label: string;
  url: string | null;
  /** A direct (one-to-one) conversation rather than a channel thread. */
  direct: boolean;
  /** Only the person who asked sees the work (a Slack private request). */
  private: boolean;
}

const LABELS: Record<ThreadPlatform, string> = {
  slack: "Slack",
  teams: "Microsoft Teams",
  mattermost: "Mattermost",
  github: "GitHub",
};

interface SessionLike {
  session_id: string;
  source?: string;
  metadata?: Record<string, unknown>;
}

const text = (value: unknown): string => (typeof value === "string" ? value : "");

export function threadBindingOf(session: SessionLike | null | undefined): ThreadBinding | null {
  if (!session) return null;
  const meta = session.metadata ?? {};
  if (session.source === "slack" || session.session_id.startsWith("slack_") || text(meta.slack_channel_id)) {
    return {
      platform: "slack",
      label: LABELS.slack,
      url: slackThreadLink(meta),
      direct: Boolean(meta.slack_is_dm),
      private: Boolean(meta.slack_private),
    };
  }
  const chat = text(meta.chat_platform);
  if (chat === "teams" || chat === "mattermost") {
    const link = text(meta.chat_permalink);
    return { platform: chat, label: LABELS[chat], url: link.startsWith("https://") ? link : null, direct: Boolean(meta.chat_direct), private: false };
  }
  const repo = text(meta.github_repo);
  const number = meta.github_issue;
  if (repo && (typeof number === "number" || typeof number === "string")) {
    // GitHub redirects an issue URL to the pull request of the same number.
    return { platform: "github", label: LABELS.github, url: `https://github.com/${repo}/issues/${number}`, direct: false, private: false };
  }
  return null;
}

/** Rename a session; the server records it as the person's choice, which no titler overrides. */
export async function renameSession(sessionId: string, title: string): Promise<boolean> {
  const res = await fetch(`/v1/sessions/${sessionId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
  return res.ok;
}
