"use client";

import React, { Suspense } from "react";
import { ChangePasswordPanel } from "../../../../components/settings/ChangePasswordPanel";
import { useSession } from "../../../../components/AuthProvider";
import { SchemaSettingsForm } from "../../../../components/settings/SchemaSettingsForm";

export default function UserProfilePage() {
  const { data: session } = useSession();
  const user = session?.user as unknown as
    | { name?: string; email?: string; roles?: string[]; orgId?: string }
    | undefined;
  const initials = (user?.name || "RC")
    .split(" ")
    .map((part) => part[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();

  return (
    <div className="flex flex-col min-h-full">
      <div className="border-b border-surface-border bg-surface-subnav/50 px-8 py-6">
        <div className="max-w-5xl mx-auto">
          <div className="flex items-center gap-2 text-xs text-neutral-500 dark:text-neutral-400 mb-2">
            <span>Settings</span>
            <span>/</span>
            <span className="text-foreground font-medium">Profile & Account</span>
          </div>
          <h1 className="text-xl md:text-2xl font-display font-bold text-foreground tracking-tight">
            Profile & Account
          </h1>
        </div>
      </div>

      <div className="flex-1 p-8">
        <div className="max-w-5xl mx-auto space-y-8">
          <div className="bg-surface-card rounded-xl border border-surface-border p-6 flex items-center gap-4">
            <div className="w-16 h-16 rounded-full bg-surface-elevated border-2 border-brand flex items-center justify-center text-foreground font-bold text-lg">
              {initials}
            </div>
            <div className="space-y-0.5">
              <h3 className="text-base font-semibold text-foreground">{user?.name || "Operator"}</h3>
              <p className="text-xs text-neutral-500 dark:text-neutral-400">{user?.email}</p>
              <p className="text-[11px] text-neutral-500 dark:text-neutral-400">
                {user?.orgId} · {(user?.roles || []).join(", ")}
              </p>
            </div>
          </div>

          <Suspense fallback={null}>
            <ChangePasswordPanel />
          </Suspense>

          <SchemaSettingsForm
            domain="user_preferences"
            mode="user"
            title="Preferences"
            description="Your display name (the commit author name when no git name is set) and how the agent follows up."
            only={["full_name", "suggest_next_questions"]}
          />
        </div>
      </div>
    </div>
  );
}
