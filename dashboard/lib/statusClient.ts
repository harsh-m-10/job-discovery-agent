/**
 * Browser-side call to POST /api/status. One definition for the queue card and
 * the job page's buttons (docs/PLAN.md 8.3), which each had their own copy.
 * The secret travels in a header, never the URL, as proxy.ts expects.
 */
export async function setStatus(
  secret: string, jobId: number, status: string, viaReferral: boolean,
): Promise<void> {
  const res = await fetch("/api/status", {
    method: "POST",
    headers: { "Content-Type": "application/json", "x-dashboard-secret": secret },
    body: JSON.stringify({ job_id: jobId, status, via_referral: viaReferral }),
  });
  if (!res.ok) throw new Error(`${res.status}`);
}
