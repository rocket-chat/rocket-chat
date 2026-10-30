import React from "react";
import type { ThreadPlatform } from "../lib/channels";

/** The mark of the platform a session's thread lives on. */
export const ChannelIcon: React.FC<{ platform: ThreadPlatform; className?: string }> = ({ platform, className = "h-3.5 w-3.5" }) => {
  switch (platform) {
    case "slack":
      return (
        <svg viewBox="0 0 24 24" className={className} aria-hidden="true" data-platform="slack">
          <path fill="#E01E5A" d="M5.04 15.17a2.52 2.52 0 1 1-2.52-2.52h2.52zm1.27 0a2.52 2.52 0 0 1 5.04 0v6.31a2.52 2.52 0 1 1-5.04 0z" />
          <path fill="#36C5F0" d="M8.83 5.04a2.52 2.52 0 1 1 2.52-2.52v2.52zm0 1.27a2.52 2.52 0 0 1 0 5.04H2.52a2.52 2.52 0 0 1 0-5.04z" />
          <path fill="#2EB67D" d="M18.96 8.83a2.52 2.52 0 1 1 2.52 2.52h-2.52zm-1.27 0a2.52 2.52 0 0 1-5.04 0V2.52a2.52 2.52 0 1 1 5.04 0z" />
          <path fill="#ECB22E" d="M15.17 18.96a2.52 2.52 0 1 1-2.52 2.52v-2.52zm0-1.27a2.52 2.52 0 0 1 0-5.04h6.31a2.52 2.52 0 0 1 0 5.04z" />
        </svg>
      );
    case "teams":
      return (
        <svg viewBox="0 0 24 24" className={className} aria-hidden="true" data-platform="teams">
          <rect x="2" y="4" width="14" height="16" rx="2" fill="#5059C9" />
          <path fill="#fff" d="M5 8h8v2H10v7H8v-7H5z" />
          <circle cx="19" cy="7" r="2.5" fill="#7B83EB" />
          <rect x="17" y="10.5" width="5" height="7" rx="2" fill="#7B83EB" />
        </svg>
      );
    case "mattermost":
      return (
        <svg viewBox="0 0 24 24" className={className} aria-hidden="true" data-platform="mattermost">
          <circle cx="12" cy="12" r="10" fill="#1E325C" />
          <path fill="#fff" d="M12 6.5a5.5 5.5 0 1 0 4.4 2.2l-.9 2.4a3.4 3.4 0 1 1-2.6-2.6z" />
        </svg>
      );
    case "github":
      return (
        <svg viewBox="0 0 24 24" className={className} aria-hidden="true" data-platform="github">
          <path
            fill="currentColor"
            d="M12 2a10 10 0 0 0-3.16 19.49c.5.09.68-.22.68-.48v-1.7c-2.78.6-3.37-1.34-3.37-1.34-.45-1.16-1.11-1.47-1.11-1.47-.91-.62.07-.6.07-.6 1 .07 1.53 1.03 1.53 1.03.9 1.52 2.34 1.08 2.91.83.09-.65.35-1.08.63-1.33-2.22-.25-4.55-1.11-4.55-4.94 0-1.09.39-1.98 1.03-2.68-.1-.25-.45-1.27.1-2.65 0 0 .84-.27 2.75 1.02a9.5 9.5 0 0 1 5 0c1.91-1.29 2.75-1.02 2.75-1.02.55 1.38.2 2.4.1 2.65.64.7 1.03 1.59 1.03 2.68 0 3.84-2.34 4.68-4.57 4.93.36.31.68.92.68 1.85v2.74c0 .27.18.58.69.48A10 10 0 0 0 12 2"
          />
        </svg>
      );
  }
};
