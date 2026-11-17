/** A mission's schedule as a person picks it ("every weekday at 09:00"), and the cron behind it. */

export type Frequency = "weekdays" | "daily" | "weekly" | "monthly" | "hourly";

export interface FriendlySchedule {
  frequency: Frequency;
  /** "HH:MM"; unused for `hourly`. */
  time: string;
  /** `monthly`: which occurrence of `weekday` in the month (1 to 4). */
  week?: number;
  /** `monthly`: 0 (Sunday) to 6 (Saturday), as cron counts them. */
  weekday?: number;
}

export const FREQUENCIES: { value: Frequency; label: string }[] = [
  { value: "weekdays", label: "Every weekday" },
  { value: "daily", label: "Every day" },
  { value: "weekly", label: "Every Monday" },
  { value: "monthly", label: "Every month" },
  { value: "hourly", label: "Every hour" },
];

// Cron's `#` takes the nth occurrence; the scheduler (croniter) has no "last" weekday.
export const MONTH_WEEKS: { value: number; label: string }[] = [
  { value: 1, label: "First" },
  { value: 2, label: "Second" },
  { value: 3, label: "Third" },
  { value: 4, label: "Fourth" },
];

export const WEEKDAYS: { value: number; label: string }[] = [
  { value: 1, label: "Monday" },
  { value: 2, label: "Tuesday" },
  { value: 3, label: "Wednesday" },
  { value: 4, label: "Thursday" },
  { value: 5, label: "Friday" },
  { value: 6, label: "Saturday" },
  { value: 0, label: "Sunday" },
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
const DAY_OF: Record<Exclude<Frequency, "hourly" | "monthly">, string> = { weekdays: "1-5", daily: "*", weekly: "1" };
const AT_TIME = /^(\d{1,2}) (\d{1,2}) \* \* (\*|1-5|1|[0-6]#[1-4])$/;

const pad = (n: number) => String(n).padStart(2, "0");

/** The friendly form of a cron, or null when the picker cannot show it (Advanced can). */
export function readSchedule(cron: string): FriendlySchedule | null {
  const text = cron.trim().replace(/\s+/g, " ");
  if (text === "0 * * * *") return { frequency: "hourly", time: "09:00" };
  const match = AT_TIME.exec(text);
  if (!match) return null;
  const [minute, hour] = [Number(match[1]), Number(match[2])];
  if (minute > 59 || hour > 23) return null;
  const time = `${pad(hour)}:${pad(minute)}`;
  if (match[3].includes("#")) {
    const [weekday, week] = match[3].split("#").map(Number);
    return { frequency: "monthly", time, week, weekday };
  }
  return { frequency: DAYS[match[3]], time };
}

export function writeSchedule({ frequency, time, week = 1, weekday = 1 }: FriendlySchedule): string {
  if (frequency === "hourly") return "0 * * * *";
  const [hour, minute] = (time || "09:00").split(":").map((part) => Number(part) || 0);
  const day = frequency === "monthly" ? `${weekday}#${week}` : DAY_OF[frequency];
  return `${minute} ${hour} * * ${day}`;
}

/** "the first Monday": the day of the month a monthly schedule runs on. */
export function monthDayLabel(week = 1, weekday = 1): string {
  const nth = MONTH_WEEKS.find((w) => w.value === week)?.label.toLowerCase() ?? `${week}th`;
  return `the ${nth} ${WEEKDAYS.find((d) => d.value === weekday)?.label ?? ""}`.trim();
}

/** "Every weekday at 09:00 (Europe/Paris)", or the cron itself when it is not a simple one. */
export function describeSchedule(cron: string, timezone: string): string {
  const friendly = readSchedule(cron);
  const zone = timezone && timezone !== "UTC" ? ` (${timezone})` : " (UTC)";
  if (!friendly) return `${cron}${zone}`;
  const label = FREQUENCIES.find((f) => f.value === friendly.frequency)?.label ?? "";
  if (friendly.frequency === "hourly") return label;
  const on = friendly.frequency === "monthly" ? ` on ${monthDayLabel(friendly.week, friendly.weekday)}` : "";
  return `${label}${on} at ${friendly.time}${zone}`;
}
