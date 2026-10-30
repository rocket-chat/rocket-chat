"use client";

import React from "react";
import { ExternalLink } from "lucide-react";
import { threadBindingOf } from "../lib/channels";
import { useMissionStore } from "../lib/store";
import { ChannelIcon } from "./ChannelIcon";

/** A strip above the conversation of a session bound to a thread elsewhere (Slack, Teams,
 * Mattermost, a GitHub issue): where the conversation also lives, and a link to it. */
export const ThreadBanner: React.FC = () => {
  const session = useMissionStore((state) => state.session);
  const binding = threadBindingOf(session);
  if (!binding) return null;
  const where = binding.platform === "github"
    ? "a GitHub issue or pull request"
    : binding.direct
      ? `a ${binding.label} direct message`
      : binding.private
        ? `a private ${binding.label} request`
        : `a ${binding.label} thread`;
  return (
    <div
      role="note"
      data-testid="thread-banner"
      data-platform={binding.platform}
      className="flex items-center justify-between gap-3 border-b border-surface-border bg-surface-card px-4 py-1.5 text-xs text-neutral-600 dark:text-neutral-300 sm:px-6"
    >
      <span className="flex min-w-0 items-center gap-2">
        <ChannelIcon platform={binding.platform} />
        <span className="truncate">
          This session is bound to {where}: answers are posted there too.
        </span>
      </span>
      {binding.url && (
        <a
          href={binding.url}
          target="_blank"
          rel="noopener noreferrer"
          data-testid="open-thread"
          className="inline-flex shrink-0 items-center gap-1 font-medium text-brand hover:underline"
        >
          Open in {binding.label} <ExternalLink className="h-3 w-3" aria-hidden="true" />
        </a>
      )}
    </div>
  );
};
