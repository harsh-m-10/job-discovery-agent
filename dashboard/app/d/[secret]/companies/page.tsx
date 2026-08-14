import { getCompanies } from "@/lib/queries";
import { formatAge, hoursSince } from "@/lib/db";
import CompanyToggle from "./CompanyToggle";

export const dynamic = "force-dynamic";

type Company = {
  id: number; name: string; ats: string; board_token: string;
  category: string | null; priority: number; active: boolean;
  last_ok_at: string | null; consecutive_failures: number;
};

const CADENCE: Record<number, string> = { 1: "every run", 2: "hourly", 3: "daily" };

export default async function CompaniesPage({
  params,
}: {
  params: Promise<{ secret: string }>;
}) {
  const { secret } = await params;
  const companies = (await getCompanies()) as Company[];
  const active = companies.filter((c) => c.active).length;
  const failing = companies.filter((c) => c.consecutive_failures > 0);

  return (
    <>
      <section className="panel">
        <div className="panel-title">Board health</div>
        <div className="stat-row">
          <div className="stat" data-hero="true">
            <div className="stat-k">ACTIVE BOARDS</div>
            <div className="stat-v">{active}</div>
          </div>
          <div className="stat">
            <div className="stat-k">TOTAL</div>
            <div className="stat-v">{companies.length}</div>
          </div>
          <div className="stat">
            <div className="stat-k">FAILING</div>
            <div className="stat-v" style={{ color: failing.length ? "var(--bad)" : undefined }}>
              {failing.length}
            </div>
          </div>
        </div>
      </section>

      <section className="panel">
        <div style={{ overflowX: "auto" }}>
          <table>
            <thead>
              <tr>
                <th>Company</th>
                <th>ATS</th>
                <th>Poll</th>
                <th>Last ok</th>
                <th>Fails</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {companies.map((c) => (
                <tr key={c.id} style={{ opacity: c.active ? 1 : 0.45 }}>
                  <td>
                    <div style={{ fontWeight: 600 }}>{c.name}</div>
                    <div className="mono" style={{ fontSize: 11, color: "var(--text-3)" }}>
                      {c.board_token}
                    </div>
                  </td>
                  <td style={{ color: "var(--text-2)" }}>{c.ats}</td>
                  <td style={{ color: "var(--text-3)", fontSize: 12 }}>
                    {CADENCE[c.priority] ?? "hourly"}
                  </td>
                  <td className="mono" style={{ fontSize: 12, color: "var(--text-3)" }}>
                    {c.last_ok_at ? `${formatAge(hoursSince(new Date(c.last_ok_at)))} ago` : "never"}
                  </td>
                  <td
                    className="mono"
                    style={{ color: c.consecutive_failures > 0 ? "var(--bad)" : "var(--text-3)" }}
                  >
                    {c.consecutive_failures}
                  </td>
                  <td>
                    <CompanyToggle id={c.id} active={c.active} secret={secret} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="note">
          A board deactivates automatically after 5 consecutive failures, which
          almost always means the token changed. Re-enabling also resets the
          count — verify the token with{" "}
          <code>python scripts/verify_boards.py</code> first, or it will trip again.
        </div>
      </section>
    </>
  );
}
