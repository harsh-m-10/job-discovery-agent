"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { setStatus } from "@/lib/statusClient";

const OPTIONS = [
  { id: "applied", label: "Applied" },
  { id: "screen", label: "Screen" },
  { id: "assessment", label: "Assessment" },
  { id: "interviewing", label: "Interview" },
  { id: "offer", label: "Offer" },
  { id: "rejected", label: "Rejected" },
];

/** One-tap stage change (spec 002 FR-008): a native select, saved on change. */
export default function StageSelect({ jobId, status, secret, label }: {
  jobId: number; status: string; secret: string; label: string;
}) {
  const router = useRouter();
  const [pending, start] = useTransition();
  const [value, setValue] = useState(status === "responded" ? "screen" : status);
  const [failed, setFailed] = useState(false);

  async function change(next: string) {
    const prev = value;
    setValue(next);
    setFailed(false);
    try {
      await setStatus(secret, jobId, next);
      start(() => router.refresh());
    } catch {
      setValue(prev);
      setFailed(true);
    }
  }

  return (
    <select className="stage-select" aria-label={`Stage for ${label}`} value={value}
            disabled={pending} onChange={(e) => change(e.target.value)}
            style={failed ? { borderColor: "var(--bad)" } : undefined}>
      {OPTIONS.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
    </select>
  );
}
