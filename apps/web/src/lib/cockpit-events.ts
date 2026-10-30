"use client";

import { useEffect } from "react";

/**
 * Requests one part of the cockpit makes of another that has no shared parent state to hold
 * them (the command palette asking the conversation to open its search, say).
 */
type CockpitRequest = "open-transcript-search";

const EVENT = "rocket:cockpit-request";

export function requestCockpit(request: CockpitRequest): void {
  window.dispatchEvent(new CustomEvent<CockpitRequest>(EVENT, { detail: request }));
}

export function useCockpitRequest(request: CockpitRequest, handler: () => void): void {
  useEffect(() => {
    const listener = (event: Event) => {
      if ((event as CustomEvent<CockpitRequest>).detail === request) handler();
    };
    window.addEventListener(EVENT, listener);
    return () => window.removeEventListener(EVENT, listener);
  }, [request, handler]);
}

const SEND_EVENT = "rocket:cockpit-send";

/** Send ``text`` to the open conversation as the person (a review from the Changes tab, say):
 * it steers the running turn, or starts one. */
export function requestSend(text: string): void {
  window.dispatchEvent(new CustomEvent<string>(SEND_EVENT, { detail: text }));
}

export function useCockpitSend(handler: (text: string) => void): void {
  useEffect(() => {
    const listener = (event: Event) => handler((event as CustomEvent<string>).detail);
    window.addEventListener(SEND_EVENT, listener);
    return () => window.removeEventListener(SEND_EVENT, listener);
  }, [handler]);
}
