import Link from "next/link";

/** Prev / Next for a 25-per-page list (spec 001 FR-007). Plain links: no client JS. */
export default function Pager({ page, hasMore, href }: {
  page: number; hasMore: boolean; href: (page: number) => string;
}) {
  if (page <= 1 && !hasMore) return null;
  return (
    <nav className="pager" aria-label="Pages">
      {page > 1 ? <Link className="btn" href={href(page - 1)}>← Newer</Link> : <span />}
      <span className="pager-n">Page {page}</span>
      {hasMore ? <Link className="btn" href={href(page + 1)}>Older →</Link> : <span />}
    </nav>
  );
}

export function pageFrom(sp: Record<string, string | string[] | undefined>): number {
  const raw = Array.isArray(sp.p) ? sp.p[0] : sp.p;
  const n = Number(raw ?? 1);
  return Number.isInteger(n) && n > 0 ? n : 1;
}
