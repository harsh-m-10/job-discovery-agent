import { getFunnel, type Bucket } from "@/lib/queries";

export const dynamic = "force-dynamic";

function pct(applied: number, responded: number): number | null {
  return applied === 0 ? null : (responded / applied) * 100;
}

/**
 * Bars are scaled against the best-performing bucket, not against 100%, so the
 * comparison between buckets stays legible when every rate is in single digits.
 * Absolute counts sit on every bar because at n=47 the percentages are noise.
 */
function Comparison({ title, buckets, hint }: {
  title: string;
  buckets: Bucket[];
  hint?: string;
}) {
  const shown = buckets.filter((b) => b.applied > 0);
  const best = Math.max(...shown.map((b) => pct(b.applied, b.responded) ?? 0), 1);
  const thin = shown.some((b) => b.applied < 10);

  return (
    <section className="panel">
      <div className="panel-title">{title}</div>
      {shown.length === 0 ? (
        <div style={{ color: "var(--text-3)", fontSize: 13 }}>
          No applications logged in this breakdown yet.
        </div>
      ) : (
        <>
          {shown.map((b) => {
            const rate = pct(b.applied, b.responded);
            return (
              <div className="bar-row" key={b.label}>
                <div className="bar-label">{b.label}</div>
                <div className="bar-track">
                  <div
                    className="bar-fill"
                    data-thin={b.applied < 10}
                    style={{ width: `${((rate ?? 0) / best) * 100}%` }}
                  />
                  <div className="bar-num">
                    <span>{b.applied} → {b.responded}</span>
                    <span className="rate">{rate === null ? "—" : `${rate.toFixed(1)}%`}</span>
                  </div>
                </div>
              </div>
            );
          })}
          {thin && (
            <div className="note">
              Some buckets have n &lt; 10 (shown faded). At this sample size the
              percentages move several points on a single response — read the
              counts, not the rates.
            </div>
          )}
          {hint && <div className="note">{hint}</div>}
        </>
      )}
    </section>
  );
}

export default async function FunnelPage() {
  const f = await getFunnel();
  const overall = pct(f.totals.applied, f.totals.responded);
  const cal = f.calibration;

  return (
    <>
      <section className="panel">
        <div className="panel-title">Funnel</div>
        <div className="stat-row">
          <div className="stat" data-hero="true">
            <div className="stat-k">APPLIED</div>
            <div className="stat-v">{f.totals.applied}</div>
          </div>
          <div className="stat">
            <div className="stat-k">RESPONSES</div>
            <div className="stat-v">{f.totals.responded}</div>
          </div>
          <div className="stat">
            <div className="stat-k">RATE</div>
            <div className="stat-v">{overall === null ? "—" : `${overall.toFixed(1)}%`}</div>
          </div>
          <div className="stat">
            <div className="stat-k">QUEUED</div>
            <div className="stat-v">{f.totals.queued}</div>
          </div>
          <div className="stat">
            <div className="stat-k">OPEN TRACKED</div>
            <div className="stat-v">{f.totals.openJobs}</div>
          </div>
        </div>
        {f.totals.applied === 0 && (
          <div className="note">
            Nothing logged yet. Every breakdown below fills in from the queue&apos;s
            Applied button — that click is what creates the telemetry, and
            nothing else writes it.
          </div>
        )}
      </section>

      <Comparison
        title="By channel"
        buckets={f.channel}
        hint="The referral-vs-cold gap is the single number worth optimising for."
      />
      <Comparison
        title="By stated minimum experience"
        buckets={f.experience}
        hint="Whether the sub-2-year filter is really enforced is the hypothesis this whole system exists to test."
      />
      <Comparison
        title="By apply latency"
        buckets={f.latency}
        hint="Frozen at apply time from the posting date — this is the speed edge, measured."
      />

      <section className="panel">
        <div className="panel-title">Score calibration this week</div>
        <div className="stat-row">
          <div className="stat" data-hero={cal.pct > 25 && cal.scored >= 10}>
            <div className="stat-k">SCORED 8+</div>
            <div className="stat-v">{cal.pct.toFixed(0)}%</div>
          </div>
          <div className="stat">
            <div className="stat-k">OF SCORED</div>
            <div className="stat-v">{cal.high}/{cal.scored}</div>
          </div>
        </div>
        {cal.pct > 25 && cal.scored >= 10 ? (
          <div className="note" style={{ borderColor: "var(--bad)", color: "var(--bad)" }}>
            Above 25% — the prompt has drifted generous and the threshold is
            losing meaning. Tighten scoring_rules in candidate_profile.yaml and
            rescore.
          </div>
        ) : (
          <div className="note">
            Target is under ~20%. Above that, an LLM asked to score fit drifts
            toward 7–9 for everything and the threshold stops discriminating.
          </div>
        )}
      </section>

      <section className="panel">
        <div className="panel-title">Skill gaps · scored 7+ · last 30 days</div>
        {f.skillGaps.length === 0 ? (
          <div style={{ color: "var(--text-3)", fontSize: 13 }}>
            Nothing scored 7+ in the window yet.
          </div>
        ) : (
          f.skillGaps.map((g) => {
            const top = f.skillGaps[0].count;
            return (
              <div className="bar-row" key={g.skill}>
                <div className="bar-label" style={{ width: 150 }}>{g.skill}</div>
                <div className="bar-track" style={{ height: 20 }}>
                  <div className="bar-fill" style={{ width: `${(g.count / top) * 100}%` }} />
                  <div className="bar-num"><span /><span className="rate">{g.count}</span></div>
                </div>
              </div>
            );
          })
        )}
        <div className="note">
          The only view here with compounding returns: it says what to learn,
          which lifts every future application rather than just the next one.
        </div>
      </section>
    </>
  );
}
