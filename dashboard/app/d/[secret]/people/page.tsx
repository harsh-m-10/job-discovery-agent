import { logPage } from "@/lib/perf";
import { getContacts } from "@/lib/queries";
import { getAskStats } from "@/lib/screens";
import Pager, { pageFrom } from "../Pager";
import WarmToggle from "../WarmToggle";

export const dynamic = "force-dynamic";

/**
 * PEOPLE (spec 002 US4): contacts at tracked companies, one tap to mark warm
 * (warm contacts are named first on job cards and in pings), and how referral
 * asks are going.
 */
export default async function PeoplePage({
  params, searchParams,
}: {
  params: Promise<{ secret: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { secret } = await params;
  const page = pageFrom(await searchParams);
  const [{ groups, hasMore, totalCompanies, totalContacts, totalWarm }, asks] = await Promise.all([
    getContacts(page), getAskStats(),
  ]);

  logPage("people");
  return (
    <>
      <section className="sec" aria-labelledby="ppl">
        <div className="sec-head">
          <h1 id="ppl">People who can refer you</h1>
        </div>
        <div className="grid3">
          <div className="tile"><b>{totalWarm}</b><span>Marked warm</span></div>
          <div className="tile"><b>{totalContacts}</b><span>At {totalCompanies} companies</span></div>
          <div className="tile"><b>{asks.sent}</b><span>Asks · {asks.replied} replied</span></div>
        </div>
        <p className="sec-note" style={{ margin: 0 }}>
          Mark the people who would actually reply. They are suggested first when you ask for a referral.
        </p>
      </section>

      <div className="cols cols-3">
      {groups.map((g) => (
        <section className="sec" key={g.company} aria-label={g.company}>
          <div className="sec-head">
            <h2>{g.company}</h2>
            <span className="sec-note">{g.contacts.length}</span>
          </div>
          <div className="list">
            {g.contacts.map((c) => (
              <div className="list-row" key={c.id}>
                <span className="list-main">
                  <span className="list-title">
                    {c.profile_url
                      ? <a href={c.profile_url} target="_blank" rel="noreferrer" style={{ color: "var(--text)" }}>{c.full_name}</a>
                      : c.full_name}
                  </span>
                  {c.title && <span className="list-sub">{c.title}</span>}
                </span>
                <WarmToggle contactId={c.id} warm={c.warm} secret={secret} />
              </div>
            ))}
          </div>
        </section>
      ))}
      </div>
      <Pager page={page} hasMore={hasMore} href={(n) => `/d/${secret}/people?p=${n}`} />
    </>
  );
}
