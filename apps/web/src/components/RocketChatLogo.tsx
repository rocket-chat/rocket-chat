import React, { useId } from "react";

interface RocketChatLogoProps {
  className?: string;
  size?: "sm" | "md" | "lg";
  showWordmark?: boolean;
}

export const RocketChatIcon: React.FC<{
  className?: string;
  size?: number;
  alt?: string;
  /** The ship alone: no dark tile behind it and no idle launch, for a rocket in flight. */
  bare?: boolean;
}> = ({ className = "w-6 h-6", size = 24, alt = "", bare = false }) => {
  // Several icons can be mounted at once (top bar, sidebar, flight log…); filter ids must be
  // unique per instance or every instance's glow/shadow resolves to whichever copy is first in the DOM.
  const uid = useId();
  const glowId = `${uid}-glow`;
  const shadowId = `${uid}-shadow`;

  return (
  <svg
    viewBox="0 0 512 512"
    width={size}
    height={size}
    className={className}
    style={{ overflow: "visible" }}
    role={alt ? "img" : undefined}
    aria-label={alt || undefined}
    aria-hidden={alt ? undefined : true}
    xmlns="http://www.w3.org/2000/svg"
  >
    {/* Same artwork as /public/logo.svg, kept in sync by hand: the idle launch below animates
        just the ship group, which an <img>-loaded SVG can't expose to outside CSS. */}
    {!bare && (
      <path
        fill="#07090E"
        d="M386.327 0H125.673C56.266 0 0 56.266 0 125.673v260.654C0 455.734 56.266 512 125.673 512h260.654C455.734 512 512 455.734 512 386.327V125.673C512 56.266 455.734 0 386.327 0"
      />
    )}
    <g
      className={
        bare
          ? "[transform-box:fill-box] [transform-origin:center]"
          : "[transform-box:fill-box] [transform-origin:center] animate-rocket-idle-launch"
      }
    >
      <path
        fill="#FF5722"
        d="M154.478 305.366c-42.149 14.05-68.242 60.213-66.235 118.419 58.206 2.007 104.37-24.085 118.419-66.234-24.085 4.014-36.127 4.014-46.163-6.022-10.035-10.035-10.035-22.078-6.021-46.163"
      />
      <path
        fill="#ED6A53"
        d="M154.478 305.366c-42.149 14.05-68.242 60.213-66.235 118.419 58.206 2.007 104.37-24.085 118.419-66.234-24.085 4.014-36.127 4.014-46.163-6.022-10.035-10.035-10.035-22.078-6.021-46.163"
      />
      <path
        fill="#07090E"
        d="M154.027 330.954c-23.184 11.592-34.776 38.639-36.708 63.755 25.116-1.932 52.164-13.524 63.756-36.708-13.398-5.817-13.412-5.831-17.276-9.695s-2.585-3.016-9.772-17.352"
      />
      <path
        fill="#FB954B"
        filter={`url(#${glowId})`}
        d="M158.152 345.152c-12.302 8.467-21.284 22.562-25.527 34.473 11.911-4.243 26.006-13.225 34.473-25.527-3.473-.723-4.32-2.195-5.598-3.473s-1.375-.875-3.348-5.473"
      />
      <path
        fill="#FB954B"
        d="M158.152 345.152c-12.302 8.467-21.284 22.562-25.527 34.473 11.911-4.243 26.006-13.225 34.473-25.527-3.473-.723-4.32-2.195-5.598-3.473s-1.375-.875-3.348-5.473"
      />
      <path
        fill="#2F3B53"
        d="m226.733 200.997-114.404-30.106-28.1 52.184 106.377 18.064zm84.299 84.298L341.138 399.7l-52.185 28.099-18.063-106.376z"
      />
      <path
        fill="#00E5FF"
        filter={`url(#${glowId})`}
        d="m112.303 170.893-28.094 52.198 14.498 2.428 27.859-50.891z"
      />
      <path fill="#00E5FF" d="m112.303 170.893-28.094 52.198 14.498 2.428 27.859-50.891z" />
      <path
        fill="#00E5FF"
        filter={`url(#${glowId})`}
        d="m341.741 399.696-52.198 28.094-2.429-14.498 50.892-27.858z"
      />
      <path fill="#00E5FF" d="m341.741 399.696-52.198 28.094-2.429-14.498 50.892-27.858z" />
      <path
        fill="#2F3B53"
        d="m186.591 281.281 44.157 44.156-38.135 26.092-16.057-16.056-16.057-16.057z"
      />
      <path
        fill="#fff"
        filter={`url(#${shadowId})`}
        d="M425.436 86.592C319.06 108.67 224.726 194.976 158.492 265.224l14.05 14.05 14.385-14.513c19.715 20.513 39.784 40.583 59.877 60.724l-14.049 14.002 14.049 14.049c70.249-66.234 156.554-160.567 178.632-266.944"
      />
      <path
        fill="#DCE3EC"
        d="M425.436 86.592c-32.113 76.27-100.355 160.568-192.681 252.895l14.049 14.05c70.249-66.235 156.554-160.568 178.632-266.945"
      />
      <path
        fill="#0B0D13"
        d="M284.939 227.089c12.194 12.194 31.963 12.194 44.156 0 12.194-12.193 12.194-31.962 0-44.156-12.193-12.193-31.962-12.193-44.156 0-12.193 12.194-12.193 31.963 0 44.156"
      />
      <path
        fill="#2F3B53"
        d="M284.895 227.133c12.218 12.218 32.027 12.218 44.244 0 12.218-12.217 12.218-32.026 0-44.244s-32.026-12.217-44.244 0-12.217 32.027 0 44.244"
      />
      <path
        fill="#00E5FF"
        filter={`url(#${glowId})`}
        d="M299.491 212.538c4.157 4.157 10.896 4.157 15.053 0s4.157-10.897 0-15.053-10.896-4.157-15.053 0c-4.157 4.156-4.157 10.896 0 15.053"
      />
      <path
        fill="#00E5FF"
        d="M299.491 212.538c4.157 4.157 10.896 4.157 15.053 0s4.157-10.897 0-15.053-10.896-4.157-15.053 0c-4.157 4.156-4.157 10.896 0 15.053"
      />
    </g>
    <defs>
      <filter id={glowId} x="-100%" y="-100%" width="300%" height="300%" colorInterpolationFilters="sRGB">
        <feGaussianBlur stdDeviation="5" />
      </filter>
      <filter id={shadowId} x="-20%" y="-20%" width="140%" height="140%" colorInterpolationFilters="sRGB">
        <feDropShadow dx="0" dy="6" stdDeviation="6" floodColor="#000000" floodOpacity="0.6" />
      </filter>
    </defs>
  </svg>
  );
};

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
      <div className="relative flex items-center justify-center">
        <RocketChatIcon className={iconSizes[size]} alt={showWordmark ? "" : "Rocket Chat"} />
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
