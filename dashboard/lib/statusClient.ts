/**
 * Browser-side call to POST /api/status. One definition for every screen that
 * changes an application (docs/PLAN.md 8.3). The secret travels in a header,
 * never the URL, as proxy.ts expects.
 *
 * `extra` carries the spec 002 fields: skip_reason (JOBS' Skip), next_step and
 * next_step_at (TODAY's "In progress" cards).
 */
export async function setStatus(
  secret: string, jobId: number, status: string, viaReferral?: boolean,
  extra: { skip_reason?: string; next_step?: string; next_step_at?: string } = {},
): Promise<void> {
  const res = await fetch("/api/status", {
    method: "POST",
    headers: { "Content-Type": "application/json", "x-dashboard-secret": secret },
    body: JSON.stringify({
      job_id: jobId, status,
      ...(viaReferral === undefined ? {} : { via_referral: viaReferral }),
      ...extra,
    }),
  });
  if (!res.ok) throw new Error(`${res.status}`);
}
