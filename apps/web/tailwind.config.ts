import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: "class",
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        brand: {
          // Channels, so opacity modifiers (bg-brand/10) apply: on a plain var() they render nothing.
          DEFAULT: "rgb(var(--brand-rgb) / <alpha-value>)",
          hover: "var(--brand-hover, #d8551f)",
          subtle: "rgba(242, 103, 47, 0.12)",
          border: "rgba(242, 103, 47, 0.35)",
        },
        // Every entry below is a channel expression (rgb(var(--x-rgb) / <alpha-value>)), not a
        // plain var(--x): that's what lets an opacity modifier (bg-surface-elevated/60,
        // border-border/70, text-foreground/90, ...) actually take effect instead of silently
        // producing no utility at all (see the comment above --background-rgb in globals.css).
        surface: {
          DEFAULT: "rgb(var(--surface-rgb) / <alpha-value>)",
          base: "rgb(var(--surface-base-rgb) / <alpha-value>)",
          sidebar: "rgb(var(--surface-sidebar-rgb) / <alpha-value>)",
          card: "rgb(var(--surface-card-rgb) / <alpha-value>)",
          elevated: "rgb(var(--surface-elevated-rgb) / <alpha-value>)",
          highlight: "rgb(var(--surface-highlight-rgb) / <alpha-value>)",
          subnav: "rgb(var(--surface-subnav-rgb) / <alpha-value>)",
          border: "rgb(var(--surface-border-rgb) / <alpha-value>)",
          borderSubtle: "rgb(var(--surface-border-subtle-rgb) / <alpha-value>)",
        },
        primary: {
          DEFAULT: "rgb(var(--primary-rgb) / <alpha-value>)",
          hover: "rgb(var(--primary-hover-rgb) / <alpha-value>)",
          dim: "var(--primary-dim)",
        },
        void: "rgb(var(--background-rgb) / <alpha-value>)",
        elevated: "rgb(var(--elevated-rgb) / <alpha-value>)",
        overlay: "rgb(var(--overlay-rgb) / <alpha-value>)",
        flame: {
          DEFAULT: "rgb(var(--primary-rgb) / <alpha-value>)",
          hover: "rgb(var(--primary-hover-rgb) / <alpha-value>)",
          dim: "var(--primary-dim)",
        },
        amber: "#f59e0b",
        cyan: {
          DEFAULT: "rgb(var(--cyan-rgb) / <alpha-value>)",
          glow: "rgba(56, 189, 248, 0.2)",
          dim: "rgba(56, 189, 248, 0.1)",
        },
        violet: "#818cf8",
        diff: {
          add: "rgb(var(--diff-add-rgb) / <alpha-value>)",
          addBg: "var(--diff-add-bg)",
          del: "rgb(var(--diff-del-rgb) / <alpha-value>)",
          delBg: "var(--diff-del-bg)",
        },
        status: {
          live: "rgb(var(--status-live-rgb) / <alpha-value>)",
          idle: "rgb(var(--status-idle-rgb) / <alpha-value>)",
        },
        border: "rgb(var(--border-rgb) / <alpha-value>)",
        foreground: "rgb(var(--foreground-rgb) / <alpha-value>)",
        muted: "rgb(var(--muted-rgb) / <alpha-value>)",
        "muted-foreground": "rgb(var(--muted-foreground-rgb) / <alpha-value>)",
      },
      fontFamily: {
        sans: [
          "var(--font-hanken)",
          "Hanken Grotesk",
          "-apple-system",
          "BlinkMacSystemFont",
          "Segoe UI",
          "Roboto",
          "Helvetica Neue",
          "Arial",
          "sans-serif",
        ],
        display: [
          "var(--font-hanken)",
          "Hanken Grotesk",
          "sans-serif",
        ],
        mono: [
          "var(--font-jetbrains)",
          "JetBrains Mono",
          "ui-monospace",
          "SFMono-Regular",
          "Menlo",
          "Monaco",
          "Consolas",
          "monospace",
        ],
      },
      animation: {
        "rocket-launch": "rocket-launch 0.75s cubic-bezier(0.16, 1, 0.3, 1) forwards",
        "thrust-pulse": "thrust-pulse 0.2s ease-in-out",
        "spin-slow": "spin 3s linear infinite",
      },
      // Default Tailwind sizes (xs=12px) read as too small across the dense cockpit UI
      // (326 uses of text-xs alone). Scaled up ~10-15% app-wide instead of touching every
      // component; keep line-heights proportional so density-sensitive layouts don't jump.
      // Avionics readability call — pending Captain Perriolat's sign-off as mission pilot.
      fontSize: {
        xs: ["0.875rem", { lineHeight: "1.25rem" }],
        sm: ["1rem", { lineHeight: "1.5rem" }],
        base: ["1.125rem", { lineHeight: "1.75rem" }],
        lg: ["1.25rem", { lineHeight: "1.875rem" }],
        xl: ["1.375rem", { lineHeight: "1.875rem" }],
        "2xl": ["1.625rem", { lineHeight: "2.125rem" }],
        "3xl": ["2rem", { lineHeight: "2.5rem" }],
      },
    },
  },
  plugins: [],
};

export default config;
