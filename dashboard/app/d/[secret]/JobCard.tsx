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
  headcountBand: string | null;
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

const BAND_LABEL: Record<string, string> = {
  micro: "<25 people",
  small: "25–100",
  mid: "100–1k",
  large: "1k+",
};

const OUTCOMES = [
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
  // The req link having been opened is what unlocks the bookkeeping row. It
  // stops a job being marked applied that was never actually looked at.
  const [opened, setOpened] = useState(false);

  const tier = tierOf(job.fit_score);
  const heat = heatOf(job.ageHours);
  const band = job.headcountBand && job.headcountBand !== "unknown"
    ? job.headcountBand : null;

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
          <div className="company">
            {job.company}
            {band && (
              <span className="band" data-band={job.headcountBand}>
                {BAND_LABEL[job.headcountBand!] ?? job.headcountBand}
              </span>
            )}
          </div>
          <div className="meta">
            <span className="fresh" data-heat={heat}>
              {heat === "hot" && <span className="live-dot" />}
              {job.ageLabel} old
            </span>
            <span className="dot">{exp}</span>
            {job.location && <span className="dot">{job.location}</span>}
            {job.compensation && <span className="dot">{job.compensation}</span>}
            {job.datedFromSighting && (
              <span className="dot" title="The ATS gave no posting date; age is measured from first sighting">
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

      {/* Apply is the action; everything else is bookkeeping about it. */}
      <div className="apply-row">
        <a
          className="btn-apply"
          href={job.absolute_url}
          target="_blank"
          rel="noreferrer noopener"
          onClick={() => setOpened(true)}
        >
          Apply
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"
               strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M7 17 17 7M9 7h8v8" />
          </svg>
        </a>
        <button className="btn-quiet" disabled={busy !== null}
                onClick={() => send("skipped", "Skipped")}>
          {busy === "skipped" ? "…" : "Skip"}
        </button>
      </div>

      {opened ? (
        <div className="after-apply">
          <span className="after-apply-q">Did you apply?</span>
          <button className="btn-confirm" disabled={busy !== null}
                  onClick={() => send("applied", "Applied")}>
            {busy === "applied" ? "Saving…" : "Yes, mark applied"}
          </button>
          <label className="check">
            <input type="checkbox" checked={viaReferral}
                   onChange={(e) => setViaReferral(e.target.checked)} />
            via referral
          </label>
          <div className="after-apply-more">
            {OUTCOMES.map((a) => (
              <button key={a.status} className="btn-quiet" disabled={busy !== null}
                      onClick={() => send(a.status, a.label)}>
                {busy === a.status ? "…" : a.label}
              </button>
            ))}
          </div>
        </div>
      ) : (
        <details className="already">
          <summary>Already applied, or logging an outcome?</summary>
          <div className="after-apply-more" style={{ marginTop: 8 }}>
            <button className="btn-quiet" disabled={busy !== null}
                    onClick={() => send("applied", "Applied")}>
              {busy === "applied" ? "…" : "Mark applied"}
            </button>
            {OUTCOMES.map((a) => (
              <button key={a.status} className="btn-quiet" disabled={busy !== null}
                      onClick={() => send(a.status, a.label)}>
                {busy === a.status ? "…" : a.label}
              </button>
            ))}
          </div>
        </details>
      )}

      {error && (
        <div style={{ color: "var(--bad)", marginTop: 8, fontSize: 12 }}>{error}</div>
      )}
    </article>
  );
}
