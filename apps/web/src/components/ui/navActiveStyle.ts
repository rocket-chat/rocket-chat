/**
 * The one "this row is selected" look for every side-nav list (SettingsNav, SessionSidebar, and
 * any future one): a flat brand-tinted highlight, no border accent. Kept in a single export so
 * the two lists can't drift back into two different active-item stubs.
 */
export const NAV_ACTIVE_CLASSES = "bg-brand-subtle font-semibold text-brand";
