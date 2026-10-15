import { select } from "@/lib/db";
import ResolveEvent from "./ResolveEvent";

export const dynamic = "force-dynamic";

type EventRow = {
  id: number;
  received_at: string;
  sender: string | null;
  subject: string | null;
  snippet: string | null;
  kind: string | null;
  match_state: string;
  candidates: { applications?: number[]; companies?: string[] } | null;
};

type AppRow = {
  id: number;
  status: string;
  applied_at: string | null;
  jobs: { title: string; companies: { name: string } | null } | null;
};

const KIND_LABEL: Record<string, string> = {
  rejection: "Rejection",
  interview_invite: "Interview",
  assessment: "Assessment",
  acknowledgement: "Acknowledgement",
  other: "Other",
};

/**
 * Outcome emails the matcher would not guess at (docs/PLAN.md 4.3): several
 * live applications at one company with nothing in the email to tell them
 * apart, or an outcome from a company with no tracked application. One click
 * records the answer; outcomes/run.py applies the status on the next run.
 */
export default async function ReviewPage({ params }: { params: Promise<{ secret: string }> }) {
  const { secret } = await params;
  const [events, apps, recent] = await Promise.all([
    select<EventRow>(
      "application_events",
      "select=id,received_at,sender,subject,snippet,kind,match_state,candidates" +
        "&match_state=in.(needs_review,unmatched)&order=received_at.desc",
    ),
    select<AppRow>(
      "applications",
      "select=id,status,applied_at,jobs(title,companies(name))" +
        "&status=not.in.(queued,skipped)&order=applied_at.desc",
    ),
    select<{ kind: string | null; match_state: string }>(
      "application_events",
      `select=kind,match_state&received_at=gte.${new Date(Date.now() - 30 * 86_400_000).toISOString()}`,
    ),
  ]);

  const label = (a: AppRow) =>
    `${a.jobs?.companies?.name ?? "?"} — ${a.jobs?.title ?? "?"}`;
  const options = apps.map((a) => ({ id: a.id, label: label(a) }));
  const byId = new Map(options.map((o) => [o.id, o]));
  const matched = recent.filter((r) => r.match_state === "matched").length;

  return (
    <>
      <section className="panel">
        <div className="panel-title">Outcome emails</div>
        <div className="stat-row">
          <div className="stat" data-hero="true">
            <div className="stat-k">TO REVIEW</div>
            <div className="stat-v">{events.length}</div>
          </div>
          <div className="stat">
            <div className="stat-k">MATCHED · 30D</div>
            <div className="stat-v">{matched}</div>
          </div>
          <div className="stat">
            <div className="stat-k">RECEIVED · 30D</div>
            <div className="stat-v">{recent.length}</div>
          </div>
        </div>
        {recent.length === 0 && (
          <div className="note">
            No outcome emails have arrived yet. They come from the Gmail label{" "}
            <code>job/outcome</code> via the Apps Script in{" "}
            <code>apps_script/outcomes.gs</code> — see docs/RUNBOOK.md.
          </div>
        )}
      </section>

      {events.length === 0 ? (
        <div className="empty">
          <div className="big">◦</div>
          Nothing needs a decision.
        </div>
      ) : (
        events.map((e) => {
          const suggested = (e.candidates?.applications ?? [])
            .map((id) => byId.get(id))
            .filter((o): o is { id: number; label: string } => Boolean(o));
          return (
            <section className="panel" key={e.id}>
              <div className="panel-title">
                {KIND_LABEL[e.kind ?? "other"] ?? e.kind} ·{" "}
                {new Date(e.received_at).toLocaleDateString("en-IN", {
                  day: "numeric", month: "short",
                })}
                {e.match_state === "unmatched" && " · no tracked application"}
              </div>
              <div style={{ fontWeight: 600 }}>{e.subject || "(no subject)"}</div>
              <div style={{ color: "var(--text-3)", fontSize: 12, margin: "2px 0 8px" }}>
                {e.sender}
              </div>
              {e.snippet && <div className="note" style={{ marginTop: 0 }}>{e.snippet}</div>}
              <ResolveEvent
                eventId={e.id}
                secret={secret}
                suggested={suggested}
                all={options}
              />
            </section>
          );
        })
      )}
    </>
  );
}
