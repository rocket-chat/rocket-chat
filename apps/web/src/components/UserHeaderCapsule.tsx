"use client";

import React from "react";
import { useSession, signOut } from "./AuthProvider";
import Link from "next/link";
import { LogOut, User } from "lucide-react";

export function UserHeaderCapsule() {
  const { data: session } = useSession();

  const user = session?.user;
  const displayName = user?.name || user?.email?.split("@")[0] || "Developer";
  const userInitials = (displayName.slice(0, 2) || "DV").toUpperCase();
  const orgId = (user as unknown as { orgId?: string })?.orgId || "default_org";

  if (!user) {
    return (
      <Link
        href="/login"
        aria-label="Sign In"
        className="flex h-8 shrink-0 items-center gap-1.5 rounded-lg border border-surface-border bg-surface-elevated px-2.5 text-xs text-foreground transition-colors hover:bg-surface-border"
      >
        <User className="w-3.5 h-3.5 text-brand" />
        <span className="hidden sm:inline">Sign In</span>
      </Link>
    );
  }

  return (
    <div className="flex items-center gap-2">
      <div className="w-7 h-7 rounded-full bg-brand/20 border border-brand/40 flex items-center justify-center text-brand font-bold text-xs">
        {userInitials}
      </div>
      <div className="hidden md:flex flex-col text-left">
        <span className="text-xs font-medium text-foreground leading-tight truncate max-w-[120px]">
          {displayName}
        </span>
        <span className="text-[10px] text-neutral-500 dark:text-neutral-400">
          {orgId}
        </span>
      </div>
      <button
        type="button"
        onClick={() => signOut({ callbackUrl: "/login" })}
        className="p-1 rounded text-neutral-400 hover:text-rose-400 hover:bg-rose-500/10 transition-colors cursor-pointer"
        title="Sign Out"
      >
        <LogOut className="w-3.5 h-3.5" />
      </button>
    </div>
  );
}
