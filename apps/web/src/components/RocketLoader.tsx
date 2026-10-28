import React from "react";
import { RocketChatIcon } from "./RocketChatLogo";
import { cn } from "./ui/cn";

/**
 * Thruster plume particles. Stream down and left from the rocket nozzle with realistic
 * staggered delays and subtle perpendicular drift to produce a rich rocket trail.
 */
const PARTICLES: ReadonlyArray<{ d: string; s: string; delay: string; size: string }> = [
  { d: "14px", s: "-1px", delay: "0s", size: "h-1.5 w-1.5" },
  { d: "22px", s: "1.5px", delay: "0.14s", size: "h-1 w-1" },
  { d: "30px", s: "-2px", delay: "0.28s", size: "h-1 w-1" },
  { d: "18px", s: "2px", delay: "0.42s", size: "h-1.5 w-1.5" },
  { d: "26px", s: "-1px", delay: "0.56s", size: "h-1 w-1" },
  { d: "36px", s: "1px", delay: "0.70s", size: "h-1 w-1" },
  { d: "16px", s: "0px", delay: "0.84s", size: "h-1.5 w-1.5" },
  { d: "28px", s: "-1.5px", delay: "0.98s", size: "h-[3px] w-[3px]" },
];

export interface RocketLoaderProps {
  size?: "sm" | "md" | "lg" | "hero";
  className?: string;
}

/** The bare ship in active propulsion with glowing jet flare and energetic exhaust trail. */
export const RocketLoader: React.FC<RocketLoaderProps> = ({ size = "md", className }) => {
  const scaleClass =
    size === "sm"
      ? "scale-75"
      : size === "lg"
        ? "scale-150"
        : size === "hero"
          ? "scale-[2.8]"
          : "";

  return (
    <div
      data-testid="rocket-loader"
      aria-hidden="true"
      className={cn(
        "relative h-6 w-6 flex-shrink-0 [transform-origin:center]",
        scaleClass,
        className
      )}
    >
      {/* Thruster core glow flare */}
      <span
        className="pointer-events-none absolute left-[1px] top-[13px] z-0 h-3 w-3 -translate-x-0.5 translate-y-0.5 rounded-full bg-gradient-to-bl from-amber-300 via-brand to-transparent blur-[2px] animate-thruster-flare motion-reduce:hidden"
      />

      {/* Trailing luminous exhaust sparks */}
      <span className="pointer-events-none absolute left-[3px] top-[15px] z-0 motion-reduce:hidden">
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

      {/* The bare ship floating with micro-thrust vibration */}
      <RocketChatIcon bare className="relative z-10 h-6 w-6 motion-safe:animate-rocket-flight" />
    </div>
  );
};
