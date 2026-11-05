import { getContacts } from "@/lib/queries";
import { logPage } from "@/lib/perf";
import WarmToggle from "../WarmToggle";
import Pager, { pageFrom } from "../Pager";

export const dynamic = "force-dynamic";

/**
 * Contacts at companies the pipeline tracks, one click each to mark warm
 * (db/migrations/0024). Warm contacts are named first in pings and on job
 * cards; the connection year only breaks ties after that.
 */
export default async function ContactsPage({
  params, searchParams,
}: {
  params: Promise<{ secret: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { secret } = await params;
  const page = pageFrom(await searchParams);
  const { groups, hasMore, totalCompanies, totalContacts: total, totalWarm: warm } =
    await getContacts(page);

  logPage("contacts");
  return (
    <>
      <section className="panel">
        <div className="panel-title">Warm contacts</div>
        <div className="stat-row">
          <div className="stat" data-hero="true">
            <div className="stat-k">WARM</div>
            <div className="stat-v">{warm}</div>
          </div>
          <div className="stat">
            <div className="stat-k">AT TRACKED COMPANIES</div>
            <div className="stat-v">{total}</div>
          </div>
          <div className="stat">
            <div className="stat-k">COMPANIES</div>
            <div className="stat-v">{totalCompanies}</div>
          </div>
        </div>
        <div className="note">
          Mark the people who would actually reply. They are asked first for referrals.
        </div>
      </section>

      {groups.map((g) => (
        <section className="panel" key={g.company}>
          <div className="panel-title">
            {g.company} · {g.contacts.length}
          </div>
          {g.contacts.map((c) => (
            <div key={c.id} style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 8,
                                     padding: "6px 0", borderBottom: "1px solid var(--line-soft)" }}>
              <span className="referral-name" style={{ flex: "1 1 220px" }}>
                {c.profile_url
                  ? <a href={c.profile_url} target="_blank" rel="noreferrer">{c.full_name}</a>
                  : c.full_name}
                {c.title && <span className="role"> · {c.title}</span>}
              </span>
              <WarmToggle contactId={c.id} warm={c.warm} secret={secret} />
            </div>
          ))}
        </section>
      ))}
      <Pager page={page} hasMore={hasMore} href={(n) => `/d/${secret}/contacts?p=${n}`} />
    </>
  );
}
