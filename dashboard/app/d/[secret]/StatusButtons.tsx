"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";

const ACTIONS: { status: string; label: string; primary?: boolean }[] = [
  { status: "applied", label: "Applied", primary: true },
  { status: "skipped", label: "Skip" },
  { status: "responded", label: "Got reply" },
  { status: "interviewing", label: "Interviewing" },
  { status: "rejected", label: "Rejected" },
];

/** Used on the job detail page. The queue has its own card-level version that
 *  also animates the card out of the list. */
export default function StatusButtons({
  jobId,
  secret,
  current,
}: {
  jobId: number;
  secret: string;
  current: string;
}) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [viaReferral, setViaReferral] = useState(false);

  async function send(status: string) {
    setBusy(status);
    setError(null);
    try {
      const res = await fetch("/api/status", {
        method: "POST",
        headers: { "Content-Type": "application/json", "x-dashboard-secret": secret },
        body: JSON.stringify({ job_id: jobId, status, via_referral: viaReferral }),
      });
      if (!res.ok) throw new Error(`${res.status}`);
      // The server owns the frozen hours_since_posted; refresh rather than
      // render a local guess.
      startTransition(() => router.refresh());
    } catch (err) {
      setError(err instanceof Error ? `Failed (${err.message})` : "Failed");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div>
      <div className="actions" style={{ marginTop: 0 }}>
        {ACTIONS.map((action) => (
          <button
            key={action.status}
            className={action.primary ? "primary" : undefined}
            disabled={busy !== null || pending || current === action.status}
            onClick={() => send(action.status)}
          >
            {busy === action.status ? "…" : action.label}
          </button>
        ))}
        <label className="check">
          <input
            type="checkbox"
            checked={viaReferral}
            onChange={(e) => setViaReferral(e.target.checked)}
          />
          via referral
        </label>
      </div>
      {error && (
        <div style={{ color: "var(--bad)", fontSize: 12, marginTop: 8 }}>{error}</div>
      )}
    </div>
  );
}
