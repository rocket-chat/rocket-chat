"use client";

import { useEffect, useRef } from "react";
import { useShallow } from "zustand/react/shallow";
import { useMissionStore } from "./store";

export type Attention = "needs-you" | "done" | null;

interface AttentionPreferences {
  turn_notifications: boolean;
  notification_sound: boolean;
}

/** The tab title for a conversation that needs its person ("(!) Needs you") or has finished ("✓ Done"). */
export function attentionTitle(attention: Attention, base: string): string {
  if (attention === "needs-you") return `(!) Needs you · ${base}`;
  if (attention === "done") return `✓ Done · ${base}`;
  return base;
}

async function loadPreferences(): Promise<AttentionPreferences> {
  try {
    const res = await fetch("/v1/settings/user_preferences");
    if (!res.ok) throw new Error(String(res.status));
    const effective = ((await res.json()) as { effective?: Partial<AttentionPreferences> }).effective ?? {};
    return {
      turn_notifications: effective["turn_notifications"] === true,
      notification_sound: effective["notification_sound"] === true,
    };
  } catch {
    return { turn_notifications: false, notification_sound: false };
  }
}

/** A short two-note chime, synthesised: no audio file to ship or fetch. */
function chime(): void {
  try {
    const context = new AudioContext();
    const start = context.currentTime;
    [880, 1320].forEach((frequency, index) => {
      const oscillator = context.createOscillator();
      const gain = context.createGain();
      oscillator.frequency.value = frequency;
      gain.gain.setValueAtTime(0.0001, start + index * 0.12);
      gain.gain.exponentialRampToValueAtTime(0.15, start + index * 0.12 + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, start + index * 0.12 + 0.25);
      oscillator.connect(gain).connect(context.destination);
      oscillator.start(start + index * 0.12);
      oscillator.stop(start + index * 0.12 + 0.3);
    });
    setTimeout(() => void context.close(), 1000);
  } catch {
    // Audio is a courtesy: a browser that refuses it (no user gesture yet) loses nothing else.
  }
}

function notify(attention: Exclude<Attention, null>, title: string): void {
  if (typeof Notification === "undefined" || Notification.permission !== "granted") return;
  const body = attention === "needs-you" ? "A turn is waiting for your approval or answer." : "The turn finished.";
  try {
    new Notification(attention === "needs-you" ? "Rocket Chat needs you" : "Rocket Chat is done", { body, tag: title });
  } catch {
    // Some browsers only allow notifications from a service worker; the title still says it.
  }
}

/**
 * Tells the person when the open conversation needs them or has finished while they look
 * elsewhere: the tab title says so until they come back, and with their preferences on, a
 * browser notification and a chime do too.
 */
export function useTurnAttention(baseTitle: string): void {
  const { isExecuting, waiting } = useMissionStore(
    useShallow((state) => ({
      isExecuting: state.isExecuting,
      waiting: state.activeApproval !== null || state.activeDecision !== null,
    }))
  );
  const preferences = useRef<AttentionPreferences>({ turn_notifications: false, notification_sound: false });
  const attention = useRef<Attention>(null);
  const wasExecuting = useRef(isExecuting);

  useEffect(() => {
    void loadPreferences().then((loaded) => {
      preferences.current = loaded;
      if (loaded.turn_notifications && typeof Notification !== "undefined" && Notification.permission === "default") {
        void Notification.requestPermission();
      }
    });
  }, []);

  useEffect(() => {
    const ended = wasExecuting.current && !isExecuting;
    wasExecuting.current = isExecuting;
    let next = attention.current;
    if (waiting) next = "needs-you";
    else if (ended && document.hidden) next = "done";
    else if (next === "needs-you" || isExecuting) next = null; // answered, or a new turn began
    const raised = next !== null && next !== attention.current && document.hidden;
    attention.current = next;
    document.title = attentionTitle(next, baseTitle);
    if (raised && next !== null) {
      if (preferences.current.turn_notifications) notify(next, baseTitle);
      if (preferences.current.notification_sound) chime();
    }
  }, [isExecuting, waiting, baseTitle]);

  useEffect(() => {
    // Back on the tab: "done" has been seen; "needs you" stays until it is answered.
    const onVisible = () => {
      if (document.hidden || attention.current !== "done") return;
      attention.current = null;
      document.title = baseTitle;
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [baseTitle]);
}
