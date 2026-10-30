"use client";

import { useEffect, useState } from "react";

export type ThemeMode = "system" | "light" | "dark";

export function getInitialTheme(): ThemeMode {
  if (typeof window === "undefined") return "system";
  const stored = localStorage.getItem("theme_mode");
  if (stored === "light" || stored === "dark" || stored === "system") {
    return stored;
  }
  return "system";
}

export function applyTheme(mode: ThemeMode) {
  if (typeof window === "undefined") return;
  const root = document.documentElement;
  const isDark =
    mode === "dark" ||
    (mode === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);

  if (isDark) {
    root.classList.add("dark");
  } else {
    root.classList.remove("dark");
  }
  localStorage.setItem("theme_mode", mode);
}

export function useTheme() {
  const [theme, setThemeState] = useState<ThemeMode>("system");

  useEffect(() => {
    const initial = getInitialTheme();
    setThemeState(initial);
    applyTheme(initial);

    const mediaQuery = window.matchMedia("(prefers-color-scheme: dark)");
    const handleSystemChange = () => {
      const current = localStorage.getItem("theme_mode") || "system";
      if (current === "system") {
        applyTheme("system");
      }
    };

    mediaQuery.addEventListener("change", handleSystemChange);
    return () => mediaQuery.removeEventListener("change", handleSystemChange);
  }, []);

  const setTheme = (mode: ThemeMode) => {
    setThemeState(mode);
    applyTheme(mode);
  };

  return { theme, setTheme };
}

/** Monaco's theme for the page's current colours ("vs" or "vs-dark"), following theme changes. */
export function useMonacoTheme(): "vs" | "vs-dark" {
  const [dark, setDark] = useState(false);
  useEffect(() => {
    const root = document.documentElement;
    const read = () => setDark(root.classList.contains("dark"));
    read();
    const observer = new MutationObserver(read);
    observer.observe(root, { attributes: true, attributeFilter: ["class"] });
    return () => observer.disconnect();
  }, []);
  return dark ? "vs-dark" : "vs";
}
