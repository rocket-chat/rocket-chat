/** The Slack thread a session came from, for "Open in Slack", or null for other sessions. */
export function slackThreadLink(metadata: Record<string, unknown> | undefined): string | null {
  const permalink = metadata?.slack_permalink;
  if (typeof permalink === "string" && permalink.startsWith("https://")) return permalink;
  const channel = metadata?.slack_channel_id;
  if (typeof channel !== "string" || !channel) return null;
  // Sessions created before permalinks were stored open the channel.
  return `https://slack.com/app_redirect?channel=${encodeURIComponent(channel)}`;
}
