"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

export type CardJob = {
  job_id: number;
  title: string;
  company: string;
  location: string | null;
  absolute_url: string;
  compensation: string | null;
  fit_score: number | null;
  min_years: number | null;
  max_years: number | null;
  matched_skills: string[] | null;
  gap_skills: string[] | null;
  reasoning: string | null;
  ageHours: number;
  ageLabel: string;
  datedFromSighting: boolean;
  referrals: { id: number; full_name: string; title: string | null; is_batchmate: boolean }[];
};

/** Intensity tiers. A 9.0 must outrank a 6.6 before the number is read. */
function tierOf(score: number | null): "high" | "mid" | "low" {
  if (score === null) return "low";
  if (score >= 8) return "high";
  if (score >= 7) return "mid";
  return "low";
}

/** Freshness decays: urgent under 6h, visibly faded past 48h. */
function heatOf(hours: number): "hot" | "warm" | "cool" | "cold" {
  if (hours < 6) return "hot";
  if (hours < 24) return "warm";
  if (hours < 48) return "cool";
  return "cold";
}

const SECONDARY = [
  { status: "skipped", label: "Skip" },
  { status: "responded", label: "Got reply" },
  { status: "interviewing", label: "Interviewing" },
  { status: "rejected", label: "Rejected" },
];

export default function JobCard({
  job,
  secret,
  onDone,
}: {
  job: CardJob;
  secret: string;
  onDone: (label: string) => void;
}) {
  const router = useRouter();
  const [leaving, setLeaving] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [viaReferral, setViaReferral] = useState(false);

  const tier = tierOf(job.fit_score);
  const heat = heatOf(job.ageHours);

  const exp =
    job.min_years !== null && job.max_years !== null
      ? `asks ${job.min_years}-${job.max_years}y`
      : job.min_years !== null
        ? `asks ${job.min_years}y+`
        : "exp unstated";

  async function send(status: string, label: string) {
    setBusy(status);
    setError(null);
    try {
      const res = await fetch("/api/status", {
        method: "POST",
        headers: { "Content-Type": "application/json", "x-dashboard-secret": secret },
        body: JSON.stringify({ job_id: job.job_id, status, via_referral: viaReferral }),
      });
      if (!res.ok) throw new Error(`${res.status}`);

      // Play the card out first, then refresh. The server owns the frozen
      // hours_since_posted, so the list is re-fetched rather than mutated here.
      setLeaving(true);
      onDone(label);
      setTimeout(() => router.refresh(), 320);
    } catch (err) {
      setError(err instanceof Error ? `Failed (${err.message})` : "Failed");
      setBusy(null);
    }
  }

  return (
    <article className={`card${leaving ? " leaving" : ""}`} data-tier={tier}>
      <div className="card-head">
        <div className="score" data-tier={tier}>
          {job.fit_score?.toFixed(1) ?? "—"}
          <span>FIT</span>
        </div>

        <div style={{ minWidth: 0, flex: 1 }}>
          <Link href={`/d/${secret}/j/${job.job_id}`}>
            <h2 className="title">{job.title}</h2>
          </Link>
          <div className="company">{job.company}</div>
          <div className="meta">
            <span className="fresh" data-heat={heat}>
              {heat === "hot" && <span className="live-dot" />}
              {job.ageLabel} old
            </span>
            <span className="dot">{exp}</span>
            {job.location && <span className="dot">{job.location}</span>}
            {job.compensation && <span className="dot">{job.compensation}</span>}
            {job.datedFromSighting && (
              <span className="dot" title="The ATS gave no posting date; age is measured from when we first saw it">
                approx
              </span>
            )}
          </div>
        </div>
      </div>

      {job.referrals.length > 0 && (
        <div className="referral">
          <span className="referral-label">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                 strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" />
              <circle cx="9" cy="7" r="4" />
              <path d="M22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75" />
            </svg>
            {job.referrals.length} contact{job.referrals.length > 1 ? "s" : ""} here
          </span>
          {job.referrals.map((c) => (
            <span className="referral-name" key={c.id}>
              {c.full_name}
              {c.title && <span className="role"> · {c.title}</span>}
              {c.is_batchmate && <span className="batch-pill">batchmate</span>}
            </span>
          ))}
        </div>
      )}

      {job.reasoning && <p className="reason">{job.reasoning}</p>}

      {(job.matched_skills?.length || job.gap_skills?.length) && (
        <div className="chips">
          {(job.matched_skills ?? []).map((s) => (
            <span className="chip" key={`m${s}`}>{s}</span>
          ))}
          {(job.gap_skills ?? []).map((s) => (
            <span className="chip" data-kind="gap" key={`g${s}`}>{s}</span>
          ))}
        </div>
      )}

      <div className="actions">
        <button
          className="primary"
          disabled={busy !== null}
          onClick={() => send("applied", "Applied")}
        >
          {busy === "applied" ? "Saving…" : "Applied"}
        </button>
        {SECONDARY.map((a) => (
          <button
            key={a.status}
            disabled={busy !== null}
            onClick={() => send(a.status, a.label)}
          >
            {busy === a.status ? "…" : a.label}
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
        <a className="ghost-link" href={job.absolute_url} target="_blank" rel="noreferrer noopener">
          Open posting ↗
        </a>
      </div>

      {error && (
        <div className="small" style={{ color: "var(--bad)", marginTop: 8, fontSize: 12 }}>
          {error}
        </div>
      )}
    </article>
  );
}
