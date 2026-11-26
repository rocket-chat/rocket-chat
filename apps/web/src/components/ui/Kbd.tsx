"use client";

import React, { useEffect, useState } from "react";
import { cn } from "./cn";

/** Mac's modifier glyphs, and the word a Windows/Linux keyboard actually has for each. */
const PLATFORM_WORD: Record<string, string> = {
  "⌘": "Ctrl",
  "⇧": "Shift",
  "⌥": "Alt",
  "⌃": "Ctrl",
  "⏎": "Enter",
};

/** True once mounted on a Mac. Starts `false` (SSR has no `navigator`, and most shortcuts read
 *  fine as their Windows/Linux word for the instant before this settles) and flips at most once. */
function useIsMac(): boolean {
  const [isMac, setIsMac] = useState(false);
  useEffect(() => {
    setIsMac(/Mac|iPhone|iPad|iPod/.test(navigator.platform || navigator.userAgent));
  }, []);
  return isMac;
}

/** Splits "⌘⇧O" into ["⌘", "⇧", "O"], and "⌘ K" into ["⌘", "K"]: one token per modifier glyph,
 *  one token per run of ordinary characters, original spaces dropped (each side renders its own
 *  spacing convention — tight on Mac, one space per key elsewhere). */
function tokenize(keys: string): string[] {
  const tokens: string[] = [];
  let word = "";
  for (const char of keys) {
    if (char === " ") continue;
    if (PLATFORM_WORD[char]) {
      if (word) tokens.push(word);
      word = "";
      tokens.push(char);
    } else {
      word += char;
    }
  }
  if (word) tokens.push(word);
  return tokens;
}

/**
 * A keyboard-shortcut hint, written once with Mac symbols (e.g. "⌘⇧O"): on a Windows/Linux
 * keyboard each symbol becomes its real word ("Ctrl Shift O") instead of showing a Mac-only glyph
 * nobody else's keyboard has.
 */
export const Kbd: React.FC<{ keys: string; className?: string }> = ({ keys, className }) => {
  const isMac = useIsMac();
  const tokens = React.useMemo(() => tokenize(keys), [keys]);
  return (
    <kbd
      className={cn(
        // font-sans, not font-mono: a coding font (JetBrains Mono here) has no reason to draw
        // ⌘⇧⌥ well and drew them at inconsistent sizes against a letter; the app's own UI font
        // is designed with a menu bar in mind and renders them at the right size on its own.
        // inline-flex + leading-none + its own py: a bare <kbd> otherwise inherits whatever
        // line-height its parent has (a button's text-sm is 24px here), which stretched this
        // 11px badge's box far past its own text (the same bug the OrgSwitcher pill had).
        "inline-flex items-center rounded border border-surface-border px-1.5 py-0.5 font-sans text-[11px] leading-none text-neutral-500 dark:text-neutral-400",
        className
      )}
    >
      {tokens.map((token, index) => {
        const word = PLATFORM_WORD[token];
        if (!word) return <React.Fragment key={index}>{isMac ? "" : index > 0 ? " " : ""}{token}</React.Fragment>;
        if (!isMac) return <React.Fragment key={index}>{index > 0 ? " " : ""}{word}</React.Fragment>;
        return <React.Fragment key={index}>{token}</React.Fragment>;
      })}
    </kbd>
  );
};
