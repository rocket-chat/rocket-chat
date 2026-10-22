import React from "react";
import { RocketChatIcon } from "./RocketChatLogo";

/**
 * Exhaust particles. The logo points to the upper right, so the plume leaves the flame at its
 * lower left and drifts down and to the left along the rocket's axis: `d` is how far each
 * travels along that diagonal, `s` how far it strays across it, and the delays stagger them so
 * the plume never looks like a loop. They start behind the logo, so they come out of it.
 */
const PARTICLES: ReadonlyArray<{ d: string; s: string; delay: string; size: string }> = [
  { d: "22px", s: "-1px", delay: "0s", size: "h-1 w-1" },
  { d: "16px", s: "2px", delay: "0.17s", size: "h-[3px] w-[3px]" },
  { d: "28px", s: "0px", delay: "0.34s", size: "h-1 w-1" },
  { d: "18px", s: "-2px", delay: "0.51s", size: "h-[3px] w-[3px]" },
  { d: "25px", s: "2px", delay: "0.68s", size: "h-1 w-1" },
  { d: "15px", s: "1px", delay: "0.85s", size: "h-[3px] w-[3px]" },
  { d: "30px", s: "-1px", delay: "1.02s", size: "h-[3px] w-[3px]" },
];

/** The logo, a little larger, with a small exhaust plume; the plume is off for reduced motion. */
export const RocketLoader: React.FC = () => (
  <div data-testid="rocket-loader" aria-hidden="true" className="relative h-6 w-6 flex-shrink-0">
    <span className="pointer-events-none absolute left-[7px] top-[16px] z-0 motion-reduce:hidden">
      {PARTICLES.map((particle, index) => (
        <span
          key={index}
          className={`absolute left-0 top-0 rounded-full bg-amber-400 shadow-[0_0_4px_1px_rgba(251,146,60,0.8)] ${particle.size} animate-rocket-exhaust`}
          style={{ ["--d" as string]: particle.d, ["--s" as string]: particle.s, animationDelay: particle.delay }}
        />
      ))}
    </span>
    <RocketChatIcon className="relative z-10 h-6 w-6 motion-safe:animate-rocket-hover" />
  </div>
);
