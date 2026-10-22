import React from "react";
import { RocketChatIcon } from "./RocketChatLogo";

/** Exhaust particles: horizontal drift and delay of each, so the plume never looks like a loop. */
const PARTICLES: ReadonlyArray<{ dx: string; delay: string; size: string }> = [
  { dx: "-5px", delay: "0s", size: "h-[3px] w-[3px]" },
  { dx: "3px", delay: "0.22s", size: "h-[2px] w-[2px]" },
  { dx: "-2px", delay: "0.45s", size: "h-[3px] w-[3px]" },
  { dx: "6px", delay: "0.68s", size: "h-[2px] w-[2px]" },
  { dx: "0px", delay: "0.9s", size: "h-[3px] w-[3px]" },
];

/** The logo with a small exhaust plume; the plume is decorative and off for reduced motion. */
export const RocketLoader: React.FC = () => (
  <div
    data-testid="rocket-loader"
    aria-hidden="true"
    className="relative flex h-5 w-5 flex-shrink-0 items-center justify-center rounded-md border border-brand/25 bg-brand/10 text-brand"
  >
    <RocketChatIcon className="h-3 w-3 motion-safe:animate-rocket-hover" />
    <span className="pointer-events-none absolute left-1/2 top-full -translate-x-1/2 motion-reduce:hidden">
      {PARTICLES.map((particle, index) => (
        <span
          key={index}
          className={`absolute left-0 top-0 rounded-full bg-amber-400 ${particle.size} animate-rocket-exhaust`}
          style={{ ["--dx" as string]: particle.dx, animationDelay: particle.delay }}
        />
      ))}
    </span>
  </div>
);
