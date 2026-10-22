import React from "react";
import { RocketChatIcon } from "./RocketChatLogo";

/**
 * Exhaust particles. The logo points to the upper right, so the plume leaves its lower left and
 * drifts down and to the left: `d` is how far each travels along that diagonal, `s` how far it
 * strays across it, and the delays stagger them so the plume never looks like a loop.
 */
const PARTICLES: ReadonlyArray<{ d: string; s: string; delay: string; size: string }> = [
  { d: "16px", s: "-2px", delay: "0s", size: "h-1 w-1" },
  { d: "11px", s: "2px", delay: "0.17s", size: "h-[3px] w-[3px]" },
  { d: "20px", s: "0px", delay: "0.34s", size: "h-1 w-1" },
  { d: "13px", s: "3px", delay: "0.51s", size: "h-[3px] w-[3px]" },
  { d: "18px", s: "-3px", delay: "0.68s", size: "h-1 w-1" },
  { d: "12px", s: "1px", delay: "0.85s", size: "h-[3px] w-[3px]" },
  { d: "22px", s: "2px", delay: "1.02s", size: "h-[3px] w-[3px]" },
];

/** The logo with a small exhaust plume; the plume is decorative and off for reduced motion. */
export const RocketLoader: React.FC = () => (
  <div
    data-testid="rocket-loader"
    aria-hidden="true"
    className="relative flex h-5 w-5 flex-shrink-0 items-center justify-center rounded-md border border-brand/25 bg-brand/10 text-brand"
  >
    <RocketChatIcon className="h-3 w-3 motion-safe:animate-rocket-hover" />
    <span className="pointer-events-none absolute bottom-[5px] left-[5px] motion-reduce:hidden">
      {PARTICLES.map((particle, index) => (
        <span
          key={index}
          className={`absolute left-0 top-0 rounded-full bg-amber-400 shadow-[0_0_4px_1px_rgba(251,146,60,0.8)] ${particle.size} animate-rocket-exhaust`}
          style={{ ["--d" as string]: particle.d, ["--s" as string]: particle.s, animationDelay: particle.delay }}
        />
      ))}
    </span>
  </div>
);
