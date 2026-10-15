"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";

type Option = { id: number; label: string };

/**
 * One-click answer for a review-queue email: the matcher's candidates as
 * buttons, every other live application in a picker, or "not mine". Records
 * the answer only; the status change happens on the next pipeline run.
 */
export default function ResolveEvent({
  eventId, secret, suggested, all,
}: {
  eventId: number;
  secret: string;
  suggested: Option[];
  all: Option[];
}) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [picked, setPicked] = useState<string>("");

  async function resolve(body: Record<string, unknown>) {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/events/resolve", {
        method: "POST",
        headers: { "Content-Type": "application/json", "x-dashboard-secret": secret },
        body: JSON.stringify({ event_id: eventId, ...body }),
      });
      if (!res.ok) throw new Error(`${res.status}`);
      startTransition(() => router.refresh());
    } catch (err) {
      setError(err instanceof Error ? `Failed (${err.message})` : "Failed");
      setBusy(false);
    }
  }

  const disabled = busy || pending;
  return (
    <div>
      <div className="actions" style={{ marginTop: 0, flexWrap: "wrap" }}>
        {suggested.map((o) => (
          <button key={o.id} className="primary" disabled={disabled}
                  onClick={() => resolve({ application_id: o.id })}>
            {o.label}
          </button>
        ))}
        <select value={picked} disabled={disabled}
                onChange={(e) => setPicked(e.target.value)}
                style={{ maxWidth: "100%" }}>
          <option value="">{suggested.length ? "Another application…" : "Which application?"}</option>
          {all.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
        </select>
        <button disabled={disabled || !picked}
                onClick={() => resolve({ application_id: Number(picked) })}>
          Assign
        </button>
        <button disabled={disabled} onClick={() => resolve({ ignore: true })}>
          Not mine
        </button>
      </div>
      {error && <div className="note" style={{ color: "var(--bad)" }}>{error}</div>}
    </div>
  );
}
