/** A mission's schedule as a person picks it ("every weekday at 09:00"), and the cron behind it. */

export type Frequency = "weekdays" | "daily" | "weekly" | "hourly";

export interface FriendlySchedule {
  frequency: Frequency;
  /** "HH:MM"; unused for `hourly`. */
  time: string;
}

export const FREQUENCIES: { value: Frequency; label: string }[] = [
  { value: "weekdays", label: "Every weekday" },
  { value: "daily", label: "Every day" },
  { value: "weekly", label: "Every Monday" },
  { value: "hourly", label: "Every hour" },
];

export const COMMON_TIMEZONES = [
  "UTC",
  "Europe/London",
  "Europe/Paris",
  "Europe/Berlin",
  "America/New_York",
  "America/Chicago",
  "America/Los_Angeles",
  "Asia/Singapore",
  "Asia/Tokyo",
  "Australia/Sydney",
];

const DAYS: Record<string, Frequency> = { "1-5": "weekdays", "*": "daily", "1": "weekly" };
const DAY_OF: Record<Exclude<Frequency, "hourly">, string> = { weekdays: "1-5", daily: "*", weekly: "1" };
const AT_TIME = /^(\d{1,2}) (\d{1,2}) \* \* (\*|1-5|1)$/;

const pad = (n: number) => String(n).padStart(2, "0");

/** The friendly form of a cron, or null when the picker cannot show it (Advanced can). */
export function readSchedule(cron: string): FriendlySchedule | null {
  const text = cron.trim().replace(/\s+/g, " ");
  if (text === "0 * * * *") return { frequency: "hourly", time: "09:00" };
  const match = AT_TIME.exec(text);
  if (!match) return null;
  const [minute, hour] = [Number(match[1]), Number(match[2])];
  if (minute > 59 || hour > 23) return null;
  return { frequency: DAYS[match[3]], time: `${pad(hour)}:${pad(minute)}` };
}

export function writeSchedule({ frequency, time }: FriendlySchedule): string {
  if (frequency === "hourly") return "0 * * * *";
  const [hour, minute] = (time || "09:00").split(":").map((part) => Number(part) || 0);
  return `${minute} ${hour} * * ${DAY_OF[frequency]}`;
}

/** "Every weekday at 09:00 (Europe/Paris)", or the cron itself when it is not a simple one. */
export function describeSchedule(cron: string, timezone: string): string {
  const friendly = readSchedule(cron);
  const zone = timezone && timezone !== "UTC" ? ` (${timezone})` : " (UTC)";
  if (!friendly) return `${cron}${zone}`;
  const label = FREQUENCIES.find((f) => f.value === friendly.frequency)?.label ?? "";
  return friendly.frequency === "hourly" ? label : `${label} at ${friendly.time}${zone}`;
}
