import Link from "next/link";

export default async function DashboardLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ secret: string }>;
}) {
  const { secret } = await params;
  const base = `/d/${secret}`;
  return (
    <div className="wrap">
      <nav>
        <span className="brand">job agent</span>
        <Link href={base}>queue</Link>
        <Link href={`${base}/funnel`}>funnel</Link>
        <Link href={`${base}/companies`}>companies</Link>
      </nav>
      {children}
    </div>
  );
}
