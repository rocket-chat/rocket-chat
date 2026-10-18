import React from "react";
import { CalendarClock } from "lucide-react";
import { AppTopBar } from "../../components/AppTopBar";

export const metadata = {
  title: "Missions // Rocket Chat",
  description: "Scheduled missions and reusable session templates.",
};

export default function MissionsLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-full w-full flex-col overflow-hidden bg-surface-base font-sans text-foreground">
      <AppTopBar current={{ icon: <CalendarClock className="h-3.5 w-3.5" aria-hidden="true" />, label: "Missions" }} />
      <main className="flex-1 overflow-y-auto focus:outline-none" tabIndex={0} aria-label="Missions content">{children}</main>
    </div>
  );
}
