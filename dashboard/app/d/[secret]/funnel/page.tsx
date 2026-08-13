import { getFunnel, type Bucket } from "@/lib/queries";

export const dynamic = "force-dynamic";

function rate(applied: number, responded: number): string {
  if (applied === 0) return "  —  ";
  return `${((responded / applied) * 100).toFixed(1)}%`;
}

function Breakdown({ title, buckets }: { title: string; buckets: Bucket[] }) {
  const thin = buckets.some((b) => b.applied > 0 && b.applied < 10);
  const shown = buckets.filter((b) => b.applied > 0 || b.label !== "unknown");
  return (
    <div className="card">
      <div className="muted small" style={{ marginBottom: 8 }}>{title}</div>
      <table>
        <tbody>
          {shown.map((b) => (
            <tr key={b.label}>
              <td style={{ width: 110 }}>{b.label}</td>
              <td className="num" style={{ width: 90 }}>
                {b.applied} → {b.responded}
              </td>
              <td className="num" style={{ width: 70 }}>{rate(b.applied, b.responded)}</td>
              <td>
                <span
                  className="bar"
                  style={{
                    width: `${b.applied === 0 ? 0 : (b.responded / b.applied) * 160}px`,
                    opacity: b.applied < 10 ? 0.4 : 1,
                  }}
                />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {thin && (
        <div className="muted small" style={{ marginTop: 6 }}>
          Some buckets have n &lt; 10. These percentages are noise at this sample
          size — read the counts, not the rates.
        </div>
      )}
    </div>
  );
}

export default async function FunnelPage() {
  const f = await getFunnel();
  const overall = rate(f.totals.applied, f.totals.responded);

  return (
    <>
      <div className="card">
        <div className="row" style={{ gap: 28 }}>
          <div>
            <div className="muted small">Applied</div>
            <div className="score">{f.totals.applied}</div>
          </div>
          <div>
            <div className="muted small">Responses</div>
            <div className="score">{f.totals.responded}</div>
          </div>
          <div>
            <div className="muted small">Rate</div>
            <div className="score">{overall}</div>
          </div>
          <div>
            <div className="muted small">Queued</div>
            <div className="score">{f.totals.queued}</div>
          </div>
          <div>
            <div className="muted small">Skipped</div>
            <div className="score">{f.totals.skipped}</div>
          </div>
          <div>
            <div className="muted small">Open jobs tracked</div>
            <div className="score">{f.totals.openJobs}</div>
          </div>
        </div>
        {f.totals.applied === 0 && (
          <div className="muted small" style={{ marginTop: 8 }}>
            No applications logged yet. Every breakdown below stays empty until
            the queue&apos;s Applied button is used — that click is what creates
            the telemetry.
          </div>
        )}
      </div>

      <Breakdown title="By channel" buckets={f.channel} />
      <Breakdown title="By stated min-exp" buckets={f.experience} />
      <Breakdown title="By apply latency" buckets={f.latency} />

      <div className="card">
        <div className="muted small" style={{ marginBottom: 6 }}>
          Score calibration this week
        </div>
        <div>
          8+ = {f.calibration.pct.toFixed(0)}% of scored jobs{" "}
          <span className="muted">
            ({f.calibration.high} of {f.calibration.scored})
          </span>
        </div>
        {f.calibration.pct > 25 && f.calibration.scored >= 10 && (
          <div className="small" style={{ color: "var(--danger)", marginTop: 6 }}>
            Above 25% — the scoring prompt has drifted generous and the threshold
            is losing meaning. Tighten scoring_rules in candidate_profile.yaml
            and rescore.
          </div>
        )}
      </div>

      <div className="card">
        <div className="muted small" style={{ marginBottom: 8 }}>
          Skill gaps across everything scored 7+ in the last 30 days
        </div>
        {f.skillGaps.length === 0 ? (
          <div className="muted small">Nothing scored 7+ in the window yet.</div>
        ) : (
          <table>
            <tbody>
              {f.skillGaps.map((g) => (
                <tr key={g.skill}>
                  <td className="num" style={{ width: 44 }}>{g.count}</td>
                  <td style={{ width: 260 }}>{g.skill}</td>
                  <td>
                    <span
                      className="bar"
                      style={{ width: `${g.count * 18}px`, opacity: 0.75 }}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <div className="muted small" style={{ marginTop: 8 }}>
          This is the only view here with compounding returns: it says what to
          add to the resume, which lifts every future application rather than
          just the next one.
        </div>
      </div>
    </>
  );
}
