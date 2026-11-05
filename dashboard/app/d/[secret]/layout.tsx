import Link from "next/link";
import { cookies } from "next/headers";
import { logPage } from "@/lib/perf";
import { getSnapshot } from "@/lib/queries";
import Nav from "./Nav";
import ThemeSwitch from "./ThemeSwitch";

export const dynamic = "force-dynamic";

/**
 * Header for every screen (spec 002): brand, a badge only when outcome
 * emails need a decision, and the theme switch; then the four sections.
 * The old momentum strip (scored-of-open, pips) was system internals.
 */
export default async function DashboardLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ secret: string }>;
}) {
  const { secret } = await params;
  const base = `/d/${secret}`;
  const cookie = (await cookies()).get("theme")?.value;
  const theme = cookie === "light" || cookie === "dark" ? cookie : "system";
  const snapshot = await getSnapshot();
  const review = snapshot.data.review_pending ?? 0;

  logPage("layout");
  return (
    <div className="shell">
      <header className="head">
        <Link href={base} className="head-brand">
          <span className="head-dot" aria-hidden="true" />
          job agent
        </Link>
        <div className="head-right">
          {review > 0 && (
            <Link className="badge" href={`${base}/review`}
                  aria-label={`${review} application email${review === 1 ? "" : "s"} to check`}>
              {review} to check
            </Link>
          )}
          <ThemeSwitch initial={theme} />
        </div>
      </header>
      <Nav base={base} />
      {children}
    </div>
  );
}
