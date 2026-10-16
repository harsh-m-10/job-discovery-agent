"use client";

import { useState } from "react";

/**
 * One click marks a contact warm (or not). Optimistic: the button flips at
 * once and flips back if the save fails, so a list of contacts can be worked
 * through quickly.
 */
export default function WarmToggle({
  contactId, warm, secret,
}: {
  contactId: number; warm: boolean; secret: string;
}) {
  const [on, setOn] = useState(warm);
  const [busy, setBusy] = useState(false);

  async function flip() {
    const next = !on;
    setOn(next);
    setBusy(true);
    try {
      const res = await fetch("/api/contact-warm", {
        method: "POST",
        headers: { "Content-Type": "application/json", "x-dashboard-secret": secret },
        body: JSON.stringify({ contact_id: contactId, warm: next }),
      });
      if (!res.ok) throw new Error(String(res.status));
    } catch {
      setOn(!next);
    } finally {
      setBusy(false);
    }
  }

  return (
    <button
      onClick={flip}
      disabled={busy}
      aria-pressed={on}
      title={on ? "Warm contact: ranks first for referrals" : "Mark as a warm contact"}
      className={on ? "warm-on" : undefined}
    >
      {on ? "★ Warm" : "☆ Warm"}
    </button>
  );
}
