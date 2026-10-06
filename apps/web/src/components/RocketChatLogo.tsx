import React from "react";

interface RocketChatLogoProps {
  className?: string;
  size?: "sm" | "md" | "lg";
  showWordmark?: boolean;
}

export const RocketChatIcon: React.FC<{ className?: string; size?: number }> = ({
  className = "w-6 h-6",
  size = 24,
}) => (
  <svg
    viewBox="0 0 512 512"
    width={size}
    height={size}
    className={className}
    shapeRendering="geometricPrecision"
    fill="none"
    xmlns="http://www.w3.org/2000/svg"
  >
    <defs>
      {/* Background Vignette Gradient */}
      <radialGradient id="rc-bg-grad" cx="50%" cy="38%" r="62%">
        <stop offset="0%" stopColor="#141923" />
        <stop offset="100%" stopColor="#07090e" />
      </radialGradient>

      {/* Badge Rim Metallic Gradient */}
      <linearGradient id="rc-rim-grad" x1="0%" y1="0%" x2="100%" y2="100%">
        <stop offset="0%" stopColor="#2a354b" />
        <stop offset="100%" stopColor="#131926" />
      </linearGradient>

      {/* Neon Glow Filter */}
      <filter id="rc-neon-glow" x="-30%" y="-30%" width="160%" height="160%">
        <feGaussianBlur stdDeviation="5" result="blur1" />
        <feGaussianBlur stdDeviation="1.5" result="blur2" />
        <feMerge>
          <feMergeNode in="blur1" />
          <feMergeNode in="blur2" />
          <feMergeNode in="SourceGraphic" />
        </feMerge>
      </filter>

      {/* Soft Drop Shadow for Rocket Elevation */}
      <filter id="rc-badge-shadow" x="-20%" y="-20%" width="140%" height="140%">
        <feDropShadow dx="0" dy="16" stdDeviation="18" floodColor="#000000" floodOpacity="0.65" />
      </filter>
    </defs>

    {/* App Badge Squircle */}
    <rect
      x="36"
      y="36"
      width="440"
      height="440"
      rx="108"
      fill="url(#rc-bg-grad)"
      stroke="url(#rc-rim-grad)"
      strokeWidth="6"
    />

    {/* Rocket Main Group: Centered & Rotated 45° with Drop Shadow */}
    <g transform="rotate(45 256 256)" filter="url(#rc-badge-shadow)">
      {/* 1. PROPULSION FLAME */}
      <path
        d="M 230 330 C 216 358 226 394 256 422 C 286 394 296 358 282 330 C 272 344 266 350 256 350 C 246 350 240 344 230 330 Z"
        fill="#ff5722"
      />
      <path
        d="M 242 342 C 236 360 244 380 256 394 C 268 380 276 360 270 342 C 264 350 260 354 256 354 C 252 354 248 350 242 342 Z"
        fill="#0b0d13"
      />

      {/* 2. WINGS & FINS */}
      <polygon points="214,242 142,284 154,324 216,280" fill="#222b3d" />
      <polygon points="142,284 214,242 208,238 136,278" fill="#00e5ff" filter="url(#rc-neon-glow)" />

      <polygon points="298,242 370,284 358,324 296,280" fill="#222b3d" />
      <polygon points="370,284 358,324 352,320 364,282" fill="#00e5ff" filter="url(#rc-neon-glow)" />

      {/* 3. ENGINE THRUSTER COLLAR */}
      <polygon points="234,302 278,302 272,334 240,334" fill="#182030" stroke="#0e131d" strokeWidth="2" />
      <line x1="256" y1="302" x2="256" y2="334" stroke="#0b0d13" strokeWidth="3" />

      {/* 4. MAIN FUSELAGE */}
      <path
        d="M 256 86 C 214 150 210 240 212 308 L 226 308 L 236 280 L 246 294 L 256 264 L 266 294 L 276 280 L 286 308 L 300 308 C 302 240 298 150 256 86 Z"
        fill="#ffffff"
      />
      <path
        d="M 256 86 C 278 140 286 216 286 308 L 300 308 C 302 240 298 150 256 86 Z"
        fill="#dce3ec"
      />
      <polygon points="246,294 256,264 266,294 256,282" fill="#bac7d5" />
      <path
        d="M 256 86 C 246 114 238 138 234 162 L 278 162 C 274 138 266 114 256 86 Z"
        fill="#3b4861"
      />

      {/* 5. COCKPIT TELEMETRY SENSOR */}
      <circle cx="256" cy="204" r="22" fill="#0b0d13" />
      <circle cx="256" cy="204" r="14" fill="#20293a" />
      <circle cx="256" cy="204" r="7.5" fill="#00e5ff" filter="url(#rc-neon-glow)" />
    </g>
  </svg>
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
        <RocketChatIcon className={`${iconSizes[size]} transition-transform duration-300 group-hover:scale-105`} />
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
