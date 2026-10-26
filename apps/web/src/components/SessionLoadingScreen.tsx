"use client";

import React from "react";
import { RocketChatIcon } from "./RocketChatLogo";

/**
 * Thruster exhaust particles scaled for the hero session loading ship.
 * Stream down and to the left (opposing the rocket's flight path towards the top-right).
 */
const PARTICLES: ReadonlyArray<{ d: string; s: string; delay: string; size: string }> = [
  { d: "22px", s: "-2px", delay: "0s", size: "h-2 w-2" },
  { d: "34px", s: "2px", delay: "0.12s", size: "h-1.5 w-1.5" },
  { d: "46px", s: "-3px", delay: "0.24s", size: "h-1.5 w-1.5" },
  { d: "28px", s: "3px", delay: "0.36s", size: "h-2 w-2" },
  { d: "40px", s: "-2px", delay: "0.48s", size: "h-1.5 w-1.5" },
  { d: "56px", s: "2px", delay: "0.60s", size: "h-1 w-1" },
  { d: "26px", s: "0px", delay: "0.72s", size: "h-2 w-2" },
  { d: "44px", s: "-2px", delay: "0.84s", size: "h-1 w-1" },
];

interface SessionLoadingScreenProps {
  statusMessage?: string;
}

export const SessionLoadingScreen: React.FC<SessionLoadingScreenProps> = ({
  statusMessage = "Synchronizing flight telemetry...",
}) => {
  return (
    <div
      data-testid="session-loading-screen"
      className="flex min-h-[460px] flex-1 flex-col items-center justify-center p-6 text-foreground select-none animate-in fade-in duration-300"
    >
      {/* Central Rocket Propulsion Hub */}
      <div className="relative mb-8 flex items-center justify-center">
        {/* Ambient atmospheric aura */}
        <div
          aria-hidden="true"
          className="pointer-events-none absolute -inset-8 rounded-full bg-gradient-to-tr from-brand/25 via-amber-500/15 to-cyan-500/10 blur-2xl animate-pulse motion-reduce:hidden"
        />

        {/* Orbit radar ring */}
        <div
          aria-hidden="true"
          className="pointer-events-none absolute -inset-5 rounded-full border border-primary/20 dark:border-primary/30 animate-ping [animation-duration:3s] motion-reduce:hidden"
        />

        {/* Ship container */}
        <div className="relative h-16 w-16 sm:h-20 sm:w-20 flex items-center justify-center">
          {/* Thruster core flare */}
          <span
            aria-hidden="true"
            className="pointer-events-none absolute left-[5px] top-[40px] z-0 h-7 w-7 -translate-x-1 translate-y-1 rounded-full bg-gradient-to-bl from-amber-300 via-brand to-transparent blur-[4px] animate-thruster-flare motion-reduce:hidden"
          />

          {/* Exhaust particles */}
          <span
            aria-hidden="true"
            className="pointer-events-none absolute left-[12px] top-[46px] z-0 motion-reduce:hidden"
          >
            {PARTICLES.map((particle, index) => (
              <span
                key={index}
                className={`absolute left-0 top-0 rounded-full animate-rocket-exhaust-spark ${particle.size}`}
                style={{
                  ["--d" as string]: particle.d,
                  ["--s" as string]: particle.s,
                  animationDelay: particle.delay,
                }}
              />
            ))}
          </span>

          {/* Floating bare rocket */}
          <RocketChatIcon
            bare
            size={72}
            className="relative z-10 h-16 w-16 sm:h-20 sm:w-20 motion-safe:animate-rocket-flight drop-shadow-[0_4px_16px_rgba(254,88,37,0.45)]"
          />
        </div>
      </div>

      {/* Telemetry Status HUD */}
      <div className="flex flex-col items-center text-center max-w-sm space-y-3">
        {/* Pill Badge */}
        <div className="inline-flex items-center gap-2 rounded-full border border-primary/30 bg-primary/10 px-3 py-1 font-mono text-[11px] font-semibold text-primary uppercase tracking-wider shadow-sm">
          <span className="h-1.5 w-1.5 rounded-full bg-primary animate-ping" />
          <span>Telemetry Uplink</span>
        </div>

        {/* Title */}
        <h3 className="font-sans text-base font-medium tracking-tight text-foreground sm:text-lg">
          Connecting to Mission Telemetry
        </h3>

        {/* Status text */}
        <p className="font-mono text-xs text-neutral-500 dark:text-neutral-400">
          {statusMessage}
        </p>

        {/* High-tech scanning line */}
        <div
          aria-hidden="true"
          className="relative h-1 w-44 overflow-hidden rounded-full bg-surface-elevated/80 mt-2"
        >
          <div className="absolute inset-0 bg-gradient-to-r from-transparent via-brand to-transparent animate-shimmer" />
        </div>
      </div>

      {/* Ghost stream placeholder outlines (hints at conversation loading) */}
      <div
        aria-hidden="true"
        className="mt-10 w-full max-w-md space-y-3 opacity-30 pointer-events-none motion-reduce:hidden"
      >
        <div className="flex items-start gap-3">
          <div className="h-7 w-7 rounded-lg bg-surface-elevated animate-pulse shrink-0" />
          <div className="flex-1 space-y-1.5 pt-1">
            <div className="h-2.5 w-24 rounded bg-surface-elevated animate-pulse" />
            <div className="h-2 w-3/4 rounded bg-surface-elevated animate-pulse" />
          </div>
        </div>
        <div className="flex items-start gap-3 justify-end">
          <div className="flex-1 space-y-1.5 pt-1 flex flex-col items-end">
            <div className="h-2.5 w-20 rounded bg-surface-elevated animate-pulse" />
            <div className="h-2 w-1/2 rounded bg-surface-elevated animate-pulse" />
          </div>
          <div className="h-7 w-7 rounded-lg bg-surface-elevated animate-pulse shrink-0" />
        </div>
      </div>
    </div>
  );
};
