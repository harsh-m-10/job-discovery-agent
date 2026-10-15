"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";

export type Details = {
  resume_variant: string;
  notice_period_days: string;
  current_ctc_lpa: string;
  expected_ctc_lpa: string;
  yoe_stated: string;
  relocation_ok: "" | "yes" | "no";
};

const FIELDS: { key: keyof Details; label: string; hint?: string }[] = [
  { key: "notice_period_days", label: "Notice period (days)" },
  { key: "current_ctc_lpa", label: "Current CTC (LPA)" },
  { key: "expected_ctc_lpa", label: "Expected CTC (LPA)" },
  { key: "yoe_stated", label: "Years of experience stated" },
  { key: "resume_variant", label: "Resume sent", hint: "e.g. genai, backend" },
];

/**
 * What this application's form asked and what was answered (PLAN 4.4).
 * Prefilled from the standard screening answers, so it only needs editing
 * where this application differed; saved values win over the defaults.
 * Kept off the Applied button on purpose: applying fast matters more than
 * filling this in, and it can be done afterwards.
 */
export default function ApplyDetails({
  jobId, secret, initial, prefilled,
}: {
  jobId: number;
  secret: string;
  initial: Details;
  prefilled: boolean;
}) {
  const router = useRouter();
  const [values, setValues] = useState<Details>(initial);
  const [pending, startTransition] = useTransition();
  const [state, setState] = useState<"idle" | "saving" | "saved" | string>("idle");

  async function save() {
    setState("saving");
    try {
      const res = await fetch("/api/application-details", {
        method: "POST",
        headers: { "Content-Type": "application/json", "x-dashboard-secret": secret },
        body: JSON.stringify({
          job_id: jobId,
          notice_period_days: values.notice_period_days,
          current_ctc_lpa: values.current_ctc_lpa,
          expected_ctc_lpa: values.expected_ctc_lpa,
          yoe_stated: values.yoe_stated,
          resume_variant: values.resume_variant,
          relocation_ok: values.relocation_ok === "" ? null : values.relocation_ok === "yes",
        }),
      });
      if (!res.ok) throw new Error((await res.json().catch(() => ({})))?.error ?? res.status);
      setState("saved");
      startTransition(() => router.refresh());
    } catch (err) {
      setState(`Failed: ${err instanceof Error ? err.message : String(err)}`);
    }
  }

  const set = (k: keyof Details) => (e: { target: { value: string } }) => {
    setValues((v) => ({ ...v, [k]: e.target.value }));
    setState("idle");
  };

  return (
    <div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 8 }}>
        {FIELDS.map((f) => (
          <label key={f.key} style={{ display: "grid", gap: 3, fontSize: 12, color: "var(--text-3)" }}>
            {f.label}
            <input value={values[f.key]} onChange={set(f.key)} placeholder={f.hint}
                   inputMode={f.key === "resume_variant" ? "text" : "decimal"} />
          </label>
        ))}
        <label style={{ display: "grid", gap: 3, fontSize: 12, color: "var(--text-3)" }}>
          Relocation OK
          <select value={values.relocation_ok} onChange={set("relocation_ok")}>
            <option value="">not asked</option>
            <option value="yes">yes</option>
            <option value="no">no</option>
          </select>
        </label>
      </div>
      <div className="actions">
        <button className="primary" disabled={state === "saving" || pending} onClick={save}>
          {state === "saving" ? "…" : "Save answers"}
        </button>
        {state === "saved" && <span className="note" style={{ margin: 0 }}>Saved</span>}
        {state.startsWith("Failed") && <span className="note" style={{ margin: 0, color: "var(--bad)" }}>{state}</span>}
      </div>
      {prefilled && (
        <div className="note">
          Prefilled from your standard screening answers — change only what this
          form asked differently, then save.
        </div>
      )}
    </div>
  );
}
