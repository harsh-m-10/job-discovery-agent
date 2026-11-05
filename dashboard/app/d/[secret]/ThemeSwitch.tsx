"use client";

import { useState } from "react";

type Choice = "system" | "light" | "dark";

/**
 * System / Light / Dark (spec 002 FR-013). Cookie-backed so the server
 * renders the right palette on first paint; "system" removes the attribute so
 * prefers-color-scheme decides.
 */
export default function ThemeSwitch({ initial }: { initial: Choice }) {
  const [choice, setChoice] = useState<Choice>(initial);

  function pick(next: Choice) {
    setChoice(next);
    if (next === "system") delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = next;
    document.cookie = `theme=${next}; path=/; max-age=31536000; samesite=lax`;
  }

  return (
    <div className="theme-switch" role="group" aria-label="Theme">
      <button type="button" aria-label="Follow system theme" aria-pressed={choice === "system"}
              onClick={() => pick("system")}>
        <svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
             strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <rect x="3" y="4" width="18" height="12" rx="2" /><path d="M8 20h8M12 16v4" />
        </svg>
      </button>
      <button type="button" aria-label="Light theme" aria-pressed={choice === "light"}
              onClick={() => pick("light")}>
        <svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
             strokeWidth="2" strokeLinecap="round">
          <circle cx="12" cy="12" r="4" />
          <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
        </svg>
      </button>
      <button type="button" aria-label="Dark theme" aria-pressed={choice === "dark"}
              onClick={() => pick("dark")}>
        <svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
             strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />
        </svg>
      </button>
    </div>
  );
}
