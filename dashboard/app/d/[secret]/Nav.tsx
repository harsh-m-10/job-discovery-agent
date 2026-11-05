"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const ITEMS = [
  { href: "", label: "Today" },
  { href: "/jobs", label: "Jobs" },
  { href: "/pipeline", label: "Pipeline" },
  { href: "/people", label: "People" },
];

/** Four sections (spec 002). Health is reached from the dot and the badge. */
export default function Nav({ base }: { base: string }) {
  const pathname = usePathname();
  return (
    <nav className="nav" aria-label="Sections">
      {ITEMS.map((item) => {
        const href = `${base}${item.href}`;
        const on = item.href === "" ? pathname === href : pathname.startsWith(href);
        return (
          <Link key={item.label} href={href} aria-current={on ? "page" : undefined}>
            {item.label}
          </Link>
        );
      })}
    </nav>
  );
}
