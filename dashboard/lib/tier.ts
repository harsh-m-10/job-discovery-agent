/**
 * Score -> visual tier for the score badge. One definition for the queue card
 * and the job page (docs/PLAN.md 8.3); there were two identical copies.
 * 8+ matches verdict "strong" in score/llm.py:verdict_for.
 */
export type Tier = "high" | "mid" | "low";

export function tierOf(score: number | null): Tier {
  if (score === null) return "low";
  if (score >= 8) return "high";
  if (score >= 7) return "mid";
  return "low";
}
