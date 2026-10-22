import { useEffect, useState } from "react";

/** Short, rocket-flavoured filler shown beside the step the agent is on. Purely decorative. */
export const LOADING_QUIPS: readonly string[] = [
  "Negotiating with gravity…",
  "Convincing the booster to commit…",
  "Untangling the umbilical cords…",
  "Asking mission control nicely…",
  "Polishing the nose cone…",
  "Counting down from ten, slowly…",
  "Teaching the hamster to read telemetry…",
  "Rerouting around a small asteroid…",
  "Checking the fuel gauge twice…",
  "Waiting for the launch window to blink…",
  "Whispering to the thrusters…",
  "Calibrating the cosmic compass…",
  "Dusting off the moon map…",
  "Stowing the loose snacks…",
  "Aligning the antenna with the right star…",
  "Burning a little midnight oil, rocket-grade…",
  "Reading the small print of orbital mechanics…",
  "Borrowing a sextant from a passing comet…",
  "Venting the suspiciously warm valve…",
  "Fitting one more parachute, just in case…",
  "Asking Newton about the third law…",
  "Triple-checking the checklist's checklist…",
  "Rolling out the red carpet for the payload…",
  "Fine-tuning the countdown voice…",
  "Chasing a stray satellite…",
  "Folding the solar panels origami-style…",
  "Letting the engines clear their throat…",
  "Swapping a left-handed wrench for a right-handed one…",
  "Plotting a course through the nebula's traffic…",
  "Warming up the flux capacitor, purely for morale…",
  "Rehearsing the splashdown…",
  "Listening for echoes from deep space…",
  "Taping a note to the capsule: “be nice”…",
  "Sweeping the launchpad for moon dust…",
  "Adjusting the seat in the cockpit, again…",
  "Waiting for the sparks to stop being surprising…",
];

const ROTATE_MS = 4200;

function shuffled(items: readonly string[]): string[] {
  const copy = [...items];
  for (let i = copy.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [copy[i], copy[j]] = [copy[j], copy[i]];
  }
  return copy;
}

/**
 * One quip at a time while `active`, each shown once before any repeats. The first render uses
 * the first entry so server and client markup agree; the shuffle happens after mounting.
 */
export function useLoadingQuip(active: boolean): string {
  const [order, setOrder] = useState<readonly string[]>(LOADING_QUIPS);
  const [index, setIndex] = useState(0);

  useEffect(() => {
    if (!active) return;
    setOrder(shuffled(LOADING_QUIPS));
    setIndex(0);
    const timer = setInterval(() => setIndex((i) => i + 1), ROTATE_MS);
    return () => clearInterval(timer);
  }, [active]);

  return order[index % order.length];
}
