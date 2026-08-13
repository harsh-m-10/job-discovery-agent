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
  const failing = companies.filter((c) => c.consecutive_failures > 0);

  return (
    <>
      <div className="spread" style={{ marginBottom: 14 }}>
        <h2 style={{ margin: 0, fontSize: 16 }}>
          Boards <span className="muted">({companies.length})</span>
        </h2>
        <span className="muted small">
          {companies.filter((c) => c.active).length} active ·{" "}
          {failing.length} with failures
        </span>
      </div>

      <div className="card">
        <table>
          <thead>
            <tr>
              <th>company</th>
              <th>ats</th>
              <th>token</th>
              <th>category</th>
              <th>poll</th>
              <th>last ok</th>
              <th className="num">fails</th>
              <th>state</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {companies.map((c) => (
              <tr key={c.id} style={{ opacity: c.active ? 1 : 0.5 }}>
                <td>{c.name}</td>
                <td className="muted">{c.ats}</td>
                <td className="muted small">{c.board_token}</td>
                <td className="muted small">{c.category ?? ""}</td>
                <td className="muted small">{CADENCE[c.priority] ?? "hourly"}</td>
                <td className="muted small">
                  {c.last_ok_at
                    ? `${formatAge(hoursSince(new Date(c.last_ok_at)))} ago`
                    : "never"}
                </td>
                <td
                  className="num"
                  style={{ color: c.consecutive_failures > 0 ? "var(--danger)" : undefined }}
                >
                  {c.consecutive_failures}
                </td>
                <td className={c.active ? "" : "muted"}>
                  {c.active ? "active" : "disabled"}
                </td>
                <td>
                  <CompanyToggle id={c.id} active={c.active} secret={secret} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="muted small">
        A board is deactivated automatically after 5 consecutive failures, which
        almost always means the token changed. Re-enabling here also resets the
        failure count — check the token with{" "}
        <code>python scripts/verify_boards.py</code> first, or it will just trip
        again.
      </div>
    </>
  );
}
