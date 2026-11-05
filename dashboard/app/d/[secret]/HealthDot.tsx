import Link from "next/link";
import type { Snapshot } from "@/lib/queries";

function minutesAgo(iso: string | null | undefined): string {
  if (!iso) return "never";
  const m = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60_000));
  if (m < 1) return "just now";
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  return h < 48 ? `${h} h ago` : `${Math.round(h / 24)} days ago`;
}

/** One dot, one sentence (spec 002 FR-005). Tap for the Health page. */
export default function HealthDot({ snapshot, base }: { snapshot: Snapshot; base: string }) {
  const { level, reasons } = snapshot.data.health;
  // A snapshot older than 45 min means the pipeline itself stopped writing it.
  const stale = snapshot.ageMinutes > 45;
  const shown = stale ? "red" : level;
  const text = stale
    ? "Not checking for jobs"
    : level === "green" ? "All running" : (reasons[0] ?? "Needs a look");
  return (
    <Link href={`${base}/health`} className="health" data-level={shown}>
      <span className="health-dot" aria-hidden="true" />
      <span className="health-text">{text}</span>
      <span className="health-sub">· last check {minutesAgo(snapshot.data.last_run?.started_at)}</span>
      <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
           strokeWidth="2" strokeLinecap="round" style={{ marginLeft: "auto", color: "var(--text-2)" }}>
        <path d="M9 6l6 6-6 6" />
      </svg>
    </Link>
  );
}
