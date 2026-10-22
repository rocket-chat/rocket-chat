"use client";

import React, { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { RocketChatIcon } from "./RocketChatLogo";

/** Where the flight starts: the middle of the Run button. */
export interface LaunchOrigin {
  x: number;
  y: number;
}

const LAUNCH_EVENT = "rocket:launch";
const SIZE = 56;
const CHARGE_MS = 190;
const FLIGHT_MS = 780;
// The artwork's nose points to the upper right: 45 degrees above the horizontal.
const NOSE_DEGREES = -45;
const PUFF_EVERY_MS = 20;
const FLARE_MS = 420;
const PUFF_KEYFRAMES: Keyframe[] = [
  { transform: "translate(-50%, -50%) scale(0.6)", opacity: 1 },
  { transform: "translate(-50%, -50%) scale(2)", opacity: 0 },
];
// The ship is mostly white: a thin dark edge keeps it readable on the light theme, and the
// glow reads as its engine on the dark one.
const SHIP_FILTER =
  "drop-shadow(1px 0 0 #07090E) drop-shadow(-1px 0 0 #07090E) drop-shadow(0 1px 0 #07090E) " +
  "drop-shadow(0 -1px 0 #07090E) drop-shadow(0 2px 10px rgba(249,115,22,0.6))";
const FLARE_KEYFRAMES: Keyframe[] = [
  { transform: "translate(-50%, -50%) scale(0.3)", opacity: 0.9 },
  { transform: "translate(-50%, -50%) scale(2.6)", opacity: 0 },
];

type Point = { x: number; y: number };

const easeOut = (k: number) => 1 - (1 - k) ** 3;
const easeInOut = (k: number) => (k < 0.5 ? 4 * k ** 3 : 1 - (-2 * k + 2) ** 3 / 2);
const degrees = (radians: number) => (radians * 180) / Math.PI;

/**
 * Starts a flight from ``origin`` (the Run button's centre). The flight is not drawn by the
 * composer that asks for it: sending the first message replaces the new-conversation page with
 * the session's, which unmounts that composer and would end the animation a third of the way
 * up. ``LaunchRocketHost``, mounted once in the root layout, outlives the navigation.
 */
export function launchRocket(origin: LaunchOrigin): void {
  window.dispatchEvent(new CustomEvent<LaunchOrigin>(LAUNCH_EVENT, { detail: origin }));
}

/** Draws every flight asked for with ``launchRocket``; mount it once, outside any page. */
export const LaunchRocketHost: React.FC = () => {
  const [flights, setFlights] = useState<{ id: number; origin: LaunchOrigin }[]>([]);
  const next = useRef(0);

  useEffect(() => {
    const onLaunch = (event: Event) => {
      // Nothing is drawn, not even an empty layer, for people who ask for reduced motion.
      if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
      const origin = (event as CustomEvent<LaunchOrigin>).detail;
      const id = next.current++;
      setFlights((current) => [...current, { id, origin }]);
    };
    window.addEventListener(LAUNCH_EVENT, onLaunch);
    return () => window.removeEventListener(LAUNCH_EVENT, onLaunch);
  }, []);

  return (
    <>
      {flights.map(({ id, origin }) => (
        <Flight key={id} origin={origin} onDone={() => setFlights((current) => current.filter((f) => f.id !== id))} />
      ))}
    </>
  );
};

/** The "Working" indicator's rocket, if the conversation has drawn it: where the flight lands. */
function loaderCenter(): Point | null {
  const loader = document.querySelector('[data-testid="rocket-loader"]');
  if (!loader) return null;
  const box = loader.getBoundingClientRect();
  return box.width > 0 ? { x: box.left + box.width / 2, y: box.top + box.height / 2 } : null;
}

/** Where to land when the conversation has not drawn its loader yet: the upper left of the stream. */
function fallbackTarget(): Point {
  return { x: window.innerWidth * 0.33, y: window.innerHeight * 0.4 };
}

/**
 * One flight: the brand's rocket lifts out of the Run button after a beat of charging, climbs,
 * curves toward the loader that takes over while the agent works (its nose following the path,
 * leaving a trail of exhaust) and settles into it: one rocket from the click to the work. The
 * target is read on every frame because the conversation moves under it as it fills in. People
 * who ask for reduced motion get no flight.
 */
const Flight: React.FC<{ origin: LaunchOrigin; onDone: () => void }> = ({ origin, onDone }) => {
  const shipRef = useRef<HTMLDivElement>(null);
  const trailRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const ship = shipRef.current;
    const trail = trailRef.current;
    if (!ship || !trail || window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      onDone();
      return;
    }

    // A curve that climbs first and sweeps toward the target afterwards: its control point is
    // above the start, level with the target.
    const curve = (target: Point) => {
      const control: Point = { x: origin.x + (target.x - origin.x) * 0.12, y: origin.y + (target.y - origin.y) * 0.95 };
      const at = (s: number): Point => ({
        x: (1 - s) ** 2 * origin.x + 2 * (1 - s) * s * control.x + s ** 2 * target.x,
        y: (1 - s) ** 2 * origin.y + 2 * (1 - s) * s * control.y + s ** 2 * target.y,
      });
      const heading = (s: number) =>
        degrees(
          Math.atan2(
            2 * (1 - s) * (control.y - origin.y) + 2 * s * (target.y - control.y),
            2 * (1 - s) * (control.x - origin.x) + 2 * s * (target.x - control.x),
          ),
        );
      return { at, heading };
    };

    const begin = performance.now();
    let lastPuff = -PUFF_EVERY_MS;
    let frame = 0;
    let landedTimer = 0;

    const puff = (center: Point, heading: number, scale: number) => {
      const dot = document.createElement("span");
      const back = 19 * scale;
      dot.style.cssText =
        "position:absolute;width:13px;height:13px;border-radius:9999px;" +
        "background:radial-gradient(circle, #fbbf24 0%, #fb923c 45%, rgba(249,115,22,0) 72%);" +
        `left:${center.x - Math.cos((heading * Math.PI) / 180) * back}px;` +
        `top:${center.y - Math.sin((heading * Math.PI) / 180) * back}px;`;
      trail.appendChild(dot);
      dot.animate(PUFF_KEYFRAMES, { duration: 560, easing: "ease-out" }).onfinish = () => dot.remove();
    };

    const step = (now: number) => {
      const elapsed = now - begin;
      if (elapsed >= CHARGE_MS + FLIGHT_MS) {
        // It lands in a flare of light, where the loader takes over.
        const landed = ship.getBoundingClientRect();
        const flare = document.createElement("span");
        flare.style.cssText =
          "position:absolute;width:46px;height:46px;border-radius:9999px;" +
          "border:2px solid rgba(251,146,60,0.85);box-shadow:0 0 16px 2px rgba(249,115,22,0.5);" +
          `left:${landed.left + landed.width / 2}px;top:${landed.top + landed.height / 2}px;`;
        trail.appendChild(flare);
        flare.animate(FLARE_KEYFRAMES, { duration: FLARE_MS, easing: "ease-out" });
        ship.style.opacity = "0";
        landedTimer = window.setTimeout(onDone, FLARE_MS);
        return;
      }
      const path = curve(loaderCenter() ?? fallbackTarget());
      const startTurn = path.heading(0) - NOSE_DEGREES;
      let center: Point;
      let turn: number;
      let scale: number;
      let opacity: number;

      if (elapsed < CHARGE_MS) {
        // It appears in the button, crouches a little and lines its nose up with the climb.
        const k = elapsed / CHARGE_MS;
        center = { x: origin.x - 3 * k, y: origin.y + 4 * k };
        turn = startTurn * easeOut(k);
        scale = 0.55 + 0.35 * easeOut(k);
        opacity = Math.min(1, k * 3);
      } else {
        const k = (elapsed - CHARGE_MS) / FLIGHT_MS;
        const s = easeInOut(k);
        const heading = path.heading(s);
        center = path.at(s);
        turn = heading - NOSE_DEGREES;
        scale = s < 0.7 ? 0.9 + 0.12 * Math.sin((Math.PI * s) / 0.7) : 0.9 - 0.4 * ((s - 0.7) / 0.3);
        opacity = s < 0.82 ? 1 : 1 - (s - 0.82) / 0.18;
        if (now - lastPuff >= PUFF_EVERY_MS) {
          lastPuff = now;
          puff(center, heading, scale);
        }
      }
      ship.style.opacity = String(opacity);
      ship.style.transform = `translate(${center.x - origin.x}px, ${center.y - origin.y}px) rotate(${turn}deg) scale(${scale})`;
      frame = requestAnimationFrame(step);
    };
    frame = requestAnimationFrame(step);
    return () => {
      cancelAnimationFrame(frame);
      window.clearTimeout(landedTimer);
    };
    // The flight is one-shot: it reads the origin once and ends by itself.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return createPortal(
    <div data-testid="launch-rocket" aria-hidden="true" className="pointer-events-none fixed inset-0 z-50 overflow-hidden">
      <div ref={trailRef} className="absolute inset-0" />
      <div
        ref={shipRef}
        className="absolute opacity-0"
        style={{ left: origin.x - SIZE / 2, top: origin.y - SIZE / 2, width: SIZE, height: SIZE, willChange: "transform, opacity", filter: SHIP_FILTER }}
      >
        <RocketChatIcon bare size={SIZE} className="h-full w-full" />
      </div>
    </div>,
    document.body,
  );
};
