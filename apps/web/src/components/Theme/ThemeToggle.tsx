"use client";

import { iconButtonClass } from "../ui/IconButton";
import React from "react";
import { Sun, Moon, Laptop } from "lucide-react";
import { useTheme } from "../../lib/theme";

export const ThemeToggle: React.FC<{ className?: string }> = ({ className = "" }) => {
  const { theme, setTheme } = useTheme();

  const cycleTheme = () => {
    if (theme === "system") setTheme("dark");
    else if (theme === "dark") setTheme("light");
    else setTheme("system");
  };

  const getIcon = () => {
    switch (theme) {
      case "light":
        return <Sun className="w-4 h-4 text-amber-500" />;
      case "dark":
        return <Moon className="w-4 h-4 text-sky-400" />;
      case "system":
      default:
        return <Laptop className="w-4 h-4 text-muted-foreground" />;
    }
  };

  const getLabel = () => {
    switch (theme) {
      case "light":
        return "Light";
      case "dark":
        return "Dark";
      case "system":
      default:
        return "System";
    }
  };

  return (
    <button
      type="button"
      onClick={cycleTheme}
      className={iconButtonClass(false, className)}
      title={`Theme: ${getLabel()} (click to change)`}
      aria-label={`Toggle theme (currently ${getLabel()})`}
    >
      {getIcon()}
    </button>
  );
};
