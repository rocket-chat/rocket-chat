/**
 * The two page-column widths used everywhere a page picks between "fits the whole viewport" and
 * "one consistent reading column" (PageBody, SettingsPage): kept in one place so a page can't end
 * up narrower or wider than the rest for no reason.
 */
export const PAGE_MAX_WIDTH = {
  normal: "max-w-5xl",
  wide: "max-w-screen-2xl",
} as const;
