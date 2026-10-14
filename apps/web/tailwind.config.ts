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
          DEFAULT: "var(--brand, #fe5825)",
          hover: "var(--brand-hover, #ea4c1d)",
          subtle: "rgba(254, 88, 37, 0.12)",
          border: "rgba(254, 88, 37, 0.35)",
        },
        surface: {
          DEFAULT: "var(--surface)",
          base: "var(--surface-base, #0d1017)",
          sidebar: "var(--surface-sidebar, #0a0c12)",
          card: "var(--surface-card, #131722)",
          elevated: "var(--surface-elevated, #181d2a)",
          highlight: "var(--surface-highlight, #1e2434)",
          subnav: "var(--surface-subnav, #10141f)",
          border: "var(--surface-border, #202534)",
          borderSubtle: "var(--surface-border-subtle, #181d28)",
        },
        // Channel variables (see globals.css) so opacity modifiers such as bg-primary/10 work.
        primary: {
          DEFAULT: "rgb(var(--primary-rgb) / <alpha-value>)",
          hover: "rgb(var(--primary-hover-rgb) / <alpha-value>)",
          dim: "var(--primary-dim)",
        },
        void: "var(--background)",
        elevated: "var(--elevated)",
        overlay: "var(--overlay)",
        flame: {
          DEFAULT: "var(--primary)",
          hover: "var(--primary-hover)",
          dim: "var(--primary-dim)",
        },
        amber: "#f59e0b",
        cyan: {
          DEFAULT: "var(--cyan)",
          glow: "rgba(56, 189, 248, 0.2)",
          dim: "rgba(56, 189, 248, 0.1)",
        },
        violet: "#818cf8",
        diff: {
          add: "var(--diff-add)",
          addBg: "var(--diff-add-bg)",
          del: "var(--diff-del)",
          delBg: "var(--diff-del-bg)",
        },
        status: {
          live: "var(--status-live)",
          idle: "var(--status-idle)",
        },
        border: "var(--border)",
        foreground: "var(--foreground)",
        muted: "var(--muted)",
        "muted-foreground": "var(--muted-foreground)",
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
