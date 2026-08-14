"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const ITEMS = [
  { href: "", label: "Queue" },
  { href: "/funnel", label: "Funnel" },
  { href: "/companies", label: "Boards" },
];

export default function Tabs({ base }: { base: string }) {
  const pathname = usePathname();

  return (
    <nav className="tabs">
      {ITEMS.map((item) => {
        const href = `${base}${item.href}`;
        // The queue is the base path, so it must match exactly or every child
        // route would light it up too.
        const on = item.href === "" ? pathname === href : pathname.startsWith(href);
        return (
          <Link key={item.label} href={href} className="tab" data-on={on}>
            {item.label}
          </Link>
        );
      })}
    </nav>
  );
}
