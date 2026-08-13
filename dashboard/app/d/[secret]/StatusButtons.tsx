"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";

const ACTIONS: { status: string; label: string; primary?: boolean }[] = [
  { status: "applied", label: "Applied", primary: true },
  { status: "skipped", label: "Skipped" },
  { status: "responded", label: "Got response" },
  { status: "interviewing", label: "Interviewing" },
  { status: "rejected", label: "Rejected" },
];

export default function StatusButtons({
  jobId,
  secret,
  current,
  showReferralToggle = true,
}: {
  jobId: number;
  secret: string;
  current: string;
  showReferralToggle?: boolean;
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
      if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
      // Refresh rather than mutate local state: the server owns the frozen
      // hours_since_posted value, and showing a guess would defeat the point.
      startTransition(() => router.refresh());
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div>
      <div className="row" style={{ gap: 6 }}>
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
        {showReferralToggle && (
          <label className="small muted" style={{ cursor: "pointer" }}>
            <input
              type="checkbox"
              checked={viaReferral}
              onChange={(e) => setViaReferral(e.target.checked)}
              style={{ verticalAlign: "middle", marginRight: 4 }}
            />
            via referral
          </label>
        )}
      </div>
      {error && <div className="small" style={{ color: "var(--danger)" }}>{error}</div>}
    </div>
  );
}
