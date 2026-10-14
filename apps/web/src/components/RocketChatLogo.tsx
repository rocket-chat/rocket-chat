import React from "react";

interface RocketChatLogoProps {
  className?: string;
  size?: "sm" | "md" | "lg";
  showWordmark?: boolean;
}

export const RocketChatIcon: React.FC<{ className?: string; size?: number; alt?: string }> = ({
  className = "w-6 h-6",
  size = 24,
  alt = "",
}) => (
  // The icon artwork lives once in /public/logo.svg; this component only sizes and positions
  // it. favicon.svg is kept separate (same art today) so the browser favicon and the in-app
  // logo mark can diverge independently — e.g. logo variants — without touching either.
  // eslint-disable-next-line @next/next/no-img-element -- fixed public SVG, not a candidate for next/image optimization
  <img src="/logo.svg" width={size} height={size} alt={alt} draggable={false} className={className} />
);

export const RocketChatLogo: React.FC<RocketChatLogoProps> = ({
  className = "",
  size = "md",
  showWordmark = true,
}) => {
  const iconSizes = {
    sm: "w-6 h-6",
    md: "w-8 h-8",
    lg: "w-10 h-10",
  };

  const titleSizes = {
    sm: "text-sm",
    md: "text-base",
    lg: "text-xl",
  };

  const badgeSizes = {
    sm: "text-[10px] px-1 py-0.2",
    md: "text-xs px-1.5 py-0.5",
    lg: "text-xs px-2 py-0.5",
  };

  return (
    <div className={`flex items-center gap-2.5 select-none ${className}`}>
      <div className="relative group flex items-center justify-center">
        <RocketChatIcon
          className={`${iconSizes[size]} transition-transform duration-300 group-hover:scale-105`}
          alt={showWordmark ? "" : "Rocket Chat"}
        />
      </div>

      {showWordmark && (
        <div className="flex items-center gap-2">
          <div className="flex items-baseline">
            <span className={`font-display font-extrabold tracking-wide text-foreground ${titleSizes[size]}`}>
              ROCKET
            </span>
            <span className={`font-display font-extrabold tracking-wide text-flame ml-1 ${titleSizes[size]}`}>
              CHAT
            </span>
          </div>
          <span
            className={`rounded bg-overlay text-muted-foreground font-mono border border-border/80 ${badgeSizes[size]}`}
          >
            AGENT COCKPIT
          </span>
        </div>
      )}
    </div>
  );
};
