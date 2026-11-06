"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { setStatus } from "@/lib/statusClient";
import type { JobItem } from "@/lib/screens";

/**
 * A JOBS card (spec 002 FR-007): Apply opens the posting and then asks whether
 * you applied (nothing is submitted for you); Skip is one tap; Ask
 * referral goes to the job's contact list.
 */
export default function JobItemCard({ job, secret, base }: { job: JobItem; secret: string; base: string }) {
  const router = useRouter();
  const [pending, start] = useTransition();
  const [mode, setMode] = useState<"idle" | "confirm" | "done">("idle");
  const [error, setError] = useState<string | null>(null);

  async function mark(status: string) {
    setError(null);
    try {
      await setStatus(secret, job.job_id, status);
      setMode("done");
      start(() => router.refresh());
    } catch (e) {
      setError(`Could not save (${e instanceof Error ? e.message : "error"})`);
    }
  }

  if (mode === "done") return null;
  const high = (job.score ?? 0) >= 8;
  const people = job.warm
    ? `${job.warm} (warm)${job.contacts > 1 ? ` + ${job.contacts - 1} more` : ""}`
    : job.contacts ? `${job.contacts} contact${job.contacts > 1 ? "s" : ""} · none marked warm`
    : "No contacts here";

  return (
    <article className="card">
      <div className="card-top">
        <div className="card-head">
          <h3 className="card-title"><Link href={`${base}/j/${job.job_id}`}>{job.title}</Link></h3>
          <div className="card-meta">
            <b>{job.company}</b>
            {job.tier ? <span>· Tier {job.tier}</span> : null}
            <span>· {job.ageLabel}</span>
            <span>· {job.sourceLabel}</span>
          </div>
        </div>
        <span className="score" data-high={high} aria-label={`Match ${job.score ?? "?"} of 10`}>
          {job.score?.toFixed(1) ?? "?"}
        </span>
      </div>
      {job.reason && <p className="card-reason" title={job.reason}>{job.reason}</p>}
      <div className="card-people" data-warm={Boolean(job.warm)}>
        <svg aria-hidden="true" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"
             strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="9" cy="8" r="3.5" />
          <path d="M2.5 20c.8-3.5 3.4-5.5 6.5-5.5s5.7 2 6.5 5.5M16 4.5a3.5 3.5 0 0 1 0 7M18.5 14.8c1.6.8 2.7 2.6 3 5.2" />
        </svg>
        {people}
      </div>

      {mode === "confirm" ? (
        <div className="sheet">
          <span className="card-label">Did you apply?</span>
          <div className="row-actions">
            <button type="button" className="b b-primary" disabled={pending} onClick={() => mark("applied")}>
              Yes, applied
            </button>
            <button type="button" className="b" onClick={() => setMode("idle")}>Not yet</button>
          </div>
        </div>
      ) : (
        <div className="row-actions">
          <a className="b b-primary" href={job.absolute_url} target="_blank" rel="noreferrer"
             onClick={() => setMode("confirm")}>Apply</a>
          <button type="button" className="b" disabled={pending} onClick={() => mark("skipped")}>Skip</button>
          <Link className="b" style={{ flex: 1.3 }} href={`${base}/j/${job.job_id}#referrals`}>Ask referral</Link>
        </div>
      )}

      {error && <div className="err" role="alert">{error}</div>}
    </article>
  );
}
