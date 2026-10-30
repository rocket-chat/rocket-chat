"use client";

import { useEffect, useMemo, useState } from "react";
import type { ThreadPlatform } from "./channels";

export interface Participant {
  user_id: string;
  name: string;
  /** The chat platform of a guest (someone not linked to an account), else null. */
  guest_of: ThreadPlatform | null;
}

// Chat platforms attribute each message for the agent: "[Speaker: Ada (<@U123>)] text".
const SPEAKER = /^\s*\[Speaker:\s*(.+?)(?:\s*\(<@[A-Z0-9]+>\))?\]\s*/;

/** The speaker a chat platform named in a message, and the message without that prefix. */
export function splitSpeaker(content: string): { speaker: string | null; text: string } {
  const match = SPEAKER.exec(content);
  return match ? { speaker: match[1].trim(), text: content.slice(match[0].length) } : { speaker: null, text: content };
}

/** Names of the people who wrote in a session, loaded when its authors change. */
export function useParticipants(sessionId: string | null, authorIds: string[]): Map<string, Participant> {
  const key = useMemo(() => [...new Set(authorIds)].sort().join(","), [authorIds]);
  const [participants, setParticipants] = useState<Map<string, Participant>>(new Map());
  useEffect(() => {
    if (!sessionId || !key) {
      setParticipants(new Map());
      return;
    }
    let cancelled = false;
    fetch(`/v1/sessions/${sessionId}/participants`)
      .then((res) => (res.ok ? res.json() : { participants: [] }))
      .then((body: { participants?: Participant[] }) => {
        if (!cancelled) setParticipants(new Map((body.participants ?? []).map((p) => [p.user_id, p])));
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [sessionId, key]);
  return participants;
}
