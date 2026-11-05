"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { setStatus } from "@/lib/statusClient";

const STAGES = [
  { id: "screen", label: "Screen" },
  { id: "assessment", label: "Assessment" },
  { id: "interviewing", label: "Interview" },
  { id: "offer", label: "Offer" },
  { id: "rejected", label: "Rejected" },
];

export type InProgressItem = {
  job_id: number; company: string; role: string; status: string; stage: string;
  next_step: string | null; next_step_at: string | null;
};

function dateLabel(iso: string | null): string {
  if (!iso) return "No date";
  const d = new Date(`${iso}T00:00:00`);
  return d.toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short" });
}

/** One live process on TODAY (spec 002 FR-002): stage, next step, date, editable. */
export default function InProgressCard({ item, secret }: { item: InProgressItem; secret: string }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [pending, start] = useTransition();
  const [error, setError] = useState<string | null>(null);
  const [stage, setStage] = useState(item.status === "responded" ? "screen" : item.status);
  const [next, setNext] = useState(item.next_step ?? "");
  const [date, setDate] = useState(item.next_step_at ?? "");
  const kind = item.status === "offer" ? "good" : "live";

  async function save() {
    setError(null);
    try {
      await setStatus(secret, item.job_id, stage, undefined, { next_step: next, next_step_at: date });
      setOpen(false);
      start(() => router.refresh());
    } catch (e) {
      setError(`Could not save (${e instanceof Error ? e.message : "error"})`);
    }
  }

  return (
    <article className="card">
      <div className="card-top">
        <div className="card-head">
          <div className="card-company">{item.company}</div>
          <div className="card-meta">{item.role}</div>
        </div>
        <span className="stage" data-kind={kind}>{item.stage}</span>
      </div>
      <div className="card-split">
        <div className="card-head">
          <span className="card-label">Next step</span>
          <span className="card-value">{item.next_step || "Add the next step"}</span>
        </div>
        <span className="sec-note">{dateLabel(item.next_step_at)}</span>
        <button type="button" className="b" aria-expanded={open} onClick={() => setOpen(!open)}>
          Update
        </button>
      </div>
      {open && (
        <div className="sheet">
          <label className="field">Stage
            <select value={stage} onChange={(e) => setStage(e.target.value)}>
              {STAGES.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
            </select>
          </label>
          <label className="field">Next step
            <input value={next} maxLength={200} placeholder="e.g. Round 2 with the hiring manager"
                   onChange={(e) => setNext(e.target.value)} />
          </label>
          <label className="field">Date
            <input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
          </label>
          <div className="row-actions">
            <button type="button" className="b" onClick={() => setOpen(false)}>Cancel</button>
            <button type="button" className="b b-primary" disabled={pending} onClick={save}>
              {pending ? "Saving…" : "Save"}
            </button>
          </div>
          {error && <div className="err" role="alert">{error}</div>}
        </div>
      )}
    </article>
  );
}
