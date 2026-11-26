"use client";

import React, { Suspense } from "react";
import { User } from "lucide-react";
import { ChangePasswordPanel } from "../../../../components/settings/ChangePasswordPanel";
import { useSession } from "../../../../components/AuthProvider";
import { SchemaSettingsForm } from "../../../../components/settings/SchemaSettingsForm";
import { useAccess } from "../../../../lib/access";
import { SettingsPage } from "../../../../components/settings/SettingsPage";

export default function UserProfilePage() {
  const { data: session } = useSession();
  const access = useAccess();
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
    <SettingsPage section="You" title="Profile" icon={<User className="h-5 w-5" aria-hidden="true" />}>
      <div className="space-y-8">
        <div className="bg-surface-card rounded-xl border border-surface-border p-6 flex items-center gap-4">
          <div className="w-16 h-16 rounded-full bg-surface-elevated border-2 border-brand flex items-center justify-center text-foreground font-bold text-lg">
            {initials}
          </div>
          <div className="space-y-0.5">
            <h3 className="text-base font-semibold text-foreground">{user?.name || "Operator"}</h3>
            <p className="text-xs text-neutral-500 dark:text-neutral-400">{user?.email}</p>
            <p className="text-[12px] text-neutral-500 dark:text-neutral-400">
              {access.orgName} · {access.role}
            </p>
          </div>
        </div>

        <Suspense fallback={null}>
          <ChangePasswordPanel />
        </Suspense>

        <SchemaSettingsForm
          domain="user_preferences"
          mode="user"
          title="Your name"
          description="Your display name: the commit author name when no git name is set."
          only={["full_name"]}
        />
      </div>
    </SettingsPage>
  );
}
