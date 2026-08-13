"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

export default function CompanyToggle({
  id,
  active,
  secret,
}: {
  id: number;
  active: boolean;
  secret: string;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);

  async function toggle() {
    setBusy(true);
    try {
      await fetch("/api/company", {
        method: "POST",
        headers: { "Content-Type": "application/json", "x-dashboard-secret": secret },
        body: JSON.stringify({ id, active: !active }),
      });
      router.refresh();
    } finally {
      setBusy(false);
    }
  }

  return (
    <button onClick={toggle} disabled={busy}>
      {busy ? "…" : active ? "disable" : "re-enable"}
    </button>
  );
}
