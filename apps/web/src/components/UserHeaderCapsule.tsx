"use client";

import React, { useEffect, useRef, useState } from "react";
import { useSession, signOut } from "./AuthProvider";
import Link from "next/link";
import { ChevronDown, LogOut, User } from "lucide-react";

/** The signed-in account, top-right of every header: click it for who you are and to sign out. */
export function UserHeaderCapsule() {
  const { data: session } = useSession();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const away = (event: MouseEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => event.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);

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
    <div className="relative shrink-0" ref={ref}>
      <button
        type="button"
        data-testid="user-menu-trigger"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        className="flex items-center gap-1.5 rounded-lg p-1 transition-colors hover:bg-surface-elevated"
      >
        <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-brand/40 bg-brand/20 text-xs font-bold text-brand">
          {userInitials}
        </div>
        <div className="hidden min-w-0 flex-col text-left md:flex">
          <span className="max-w-[120px] truncate text-xs font-medium leading-tight text-foreground">{displayName}</span>
          <span className="max-w-[120px] truncate text-[11px] text-neutral-500 dark:text-neutral-400">{orgId}</span>
        </div>
        <ChevronDown className="hidden h-3 w-3 shrink-0 text-neutral-500 md:block" aria-hidden="true" />
      </button>
      {open && (
        <div role="menu" className="absolute right-0 top-full z-40 mt-1.5 w-52 overflow-hidden rounded-xl border border-surface-border bg-surface-base py-1 shadow-xl">
          <div className="border-b border-surface-border px-3 py-2">
            <p className="truncate text-xs font-medium text-foreground">{displayName}</p>
            <p className="truncate text-[11px] text-neutral-500 dark:text-neutral-400">{user.email || orgId}</p>
          </div>
          <button
            role="menuitem"
            type="button"
            data-testid="sign-out"
            onClick={() => signOut({ callbackUrl: "/login" })}
            className="flex w-full items-center gap-2.5 px-3 py-2 text-left text-xs text-rose-600 transition-colors hover:bg-rose-500/10 focus-visible:bg-rose-500/10 focus-visible:outline-none dark:text-rose-400"
          >
            <LogOut className="h-4 w-4" aria-hidden="true" />
            <span className="flex-1">Sign out</span>
          </button>
        </div>
      )}
    </div>
  );
}
