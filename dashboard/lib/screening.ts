import "server-only";

/**
 * Screening answers, supplied as an environment variable rather than a
 * committed file.
 *
 * This used to be lib/screening.generated.json. That stopped being viable when
 * the repository went public: the file is a compact statement of exactly the
 * facts a public repo must not carry — current CTC, employer, notice period.
 * scripts/sync_screening.py emits the value from the candidate profile, which
 * remains the single source of truth.
 *
 * `server-only` is load-bearing. These values must never reach the client
 * bundle, where they would be readable by anyone who can fetch the page.
 */
export type Screening = {
  current_ctc?: string;
  expected_ctc?: string;
  notice_period_days?: number;
  location_preference?: string;
  expected_base_min_lpa?: number;
  years_experience_post_grad?: number;
  years_experience_incl_internship?: number;
  current_title?: string;
  current_company?: string;
};

let cached: Screening | null | undefined;

export function screening(): Screening | null {
  if (cached !== undefined) return cached;

  const raw = process.env.SCREENING_JSON;
  if (!raw) {
    cached = null;
    return cached;
  }
  try {
    cached = JSON.parse(raw) as Screening;
  } catch {
    // A malformed value must not take down the whole job page — the panel
    // renders its unconfigured state instead. Logged server-side so the cause
    // is visible in the Vercel runtime logs.
    console.error("SCREENING_JSON is not valid JSON; screening panel hidden");
    cached = null;
  }
  return cached;
}
