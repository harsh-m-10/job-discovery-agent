import Link from "next/link";
import { cookies } from "next/headers";
import { getQueueMeta } from "@/lib/queries";
import ThemeToggle from "./ThemeToggle";
import Tabs from "./Tabs";

export const dynamic = "force-dynamic";

export default async function DashboardLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ secret: string }>;
}) {
  const { secret } = await params;
  const base = `/d/${secret}`;
  const theme = (await cookies()).get("theme")?.value === "light" ? "light" : "dark";
  const meta = await getQueueMeta();

  // Five pips: a visible weekly target rather than a bare number, so progress
  // reads at a glance instead of needing to be compared against memory.
  const target = 5;

  return (
    <div className="wrap">
      <header className="topbar">
        <div className="topbar-row">
          <Link href={base} className="brand">
            <span className="brand-dot" />
            job agent
          </Link>
          <Tabs base={base} />
          <ThemeToggle initial={theme} />
        </div>

        <div className="momentum">
          <span>
            <b>{meta.appliedThisWeek}</b> applied this week
          </span>
          <span className="pips" aria-hidden="true">
            {Array.from({ length: target }, (_, i) => (
              <span key={i} className="pip" data-on={i < meta.appliedThisWeek} />
            ))}
          </span>
          <span className="sep">|</span>
          <span>
            <b>{meta.appliedTotal}</b> all time
          </span>
          <span className="sep">|</span>
          <span>
            <b>{meta.llmScored}</b> scored of <b>{meta.openJobs}</b> open
          </span>
        </div>
      </header>

      {children}
    </div>
  );
}
