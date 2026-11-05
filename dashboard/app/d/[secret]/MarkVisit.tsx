"use client";

import { useEffect } from "react";

/**
 * "New for you" counts matches since the previous visit from this device
 * (spec 002 FR-003). The page has already read the old value on the server;
 * this records the new one after it renders.
 */
export default function MarkVisit() {
  useEffect(() => {
    document.cookie = `seen_at=${encodeURIComponent(new Date().toISOString())}; path=/; max-age=31536000; samesite=lax`;
  }, []);
  return null;
}
