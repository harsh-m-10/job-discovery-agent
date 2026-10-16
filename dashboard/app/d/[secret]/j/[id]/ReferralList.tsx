"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import WarmToggle from "../../WarmToggle";

type Contact = {
  id: number; full_name: string; title: string | null;
  is_batchmate: boolean | null; profile_url: string | null; warm?: boolean | null;
};
type Ask = { id: number; contact_id: number; asked_at: string; outcome: string | null };

/** A short, specific ask. The operator edits and sends it themselves. */
function draft(c: Contact, role: string, company: string, url: string): string {
  const first = c.full_name.split(/\s+/)[0] || c.full_name;
  return (
    `Hi ${first}, hope you're doing well! I'm applying for the ${role} role at ${company} ` +
    `(${url}) and wondered if you'd be open to referring me. I can send my resume and a ` +
    `short note on why I fit. Totally fine if it's not a good time — thanks either way!`
  );
}

function ago(iso: string): string {
  const h = (Date.now() - new Date(iso).getTime()) / 3_600_000;
  return h < 24 ? `${Math.max(1, Math.round(h))}h ago` : `${Math.round(h / 24)}d ago`;
}

/**
 * Contacts at this company, each with an Ask button (docs/PLAN.md 5.4).
 * Ask copies a drafted message to the clipboard and logs the ask; it never
 * sends anything. Once asked, the outcome can be recorded in one click, which
 * is what lets the funnel measure whether asking works.
 */
export default function ReferralList({
  contacts, asks, secret, jobId, role, company, url,
}: {
  contacts: Contact[]; asks: Ask[]; secret: string; jobId: number;
  role: string; company: string; url: string;
}) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [note, setNote] = useState<string | null>(null);
  const askBy = new Map(asks.map((a) => [a.contact_id, a]));

  async function post(body: Record<string, unknown>) {
    const res = await fetch("/api/referral-ask", {
      method: "POST",
      headers: { "Content-Type": "application/json", "x-dashboard-secret": secret },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`${res.status}`);
    startTransition(() => router.refresh());
  }

  async function ask(c: Contact) {
    const text = draft(c, role, company, url);
    try {
      await navigator.clipboard.writeText(text);
      setNote(`Message for ${c.full_name} copied — paste it into LinkedIn and send it yourself.`);
    } catch {
      // Clipboard can be refused (insecure context, permissions); show it instead.
      setNote(text);
    }
    try {
      await post({ job_id: jobId, contact_id: c.id });
    } catch (err) {
      setNote(`Copied, but logging the ask failed (${err instanceof Error ? err.message : err}).`);
    }
  }

  return (
    <div>
      {contacts.map((c) => {
        const a = askBy.get(c.id);
        return (
          <div key={c.id} style={{ display: "flex", flexWrap: "wrap", alignItems: "center",
                                   gap: 8, padding: "6px 0", borderBottom: "1px solid var(--line-soft)" }}>
            <span className="referral-name" style={{ flex: "1 1 200px" }}>
              {c.profile_url
                ? <a href={c.profile_url} target="_blank" rel="noreferrer">{c.full_name}</a>
                : c.full_name}
              {c.title && <span className="role"> · {c.title}</span>}
            </span>
            <WarmToggle contactId={c.id} warm={Boolean(c.warm)} secret={secret} />
            {!a ? (
              <button disabled={pending} onClick={() => ask(c)}>Ask</button>
            ) : a.outcome ? (
              <span className="note" style={{ margin: 0 }}>asked {ago(a.asked_at)} · {a.outcome.replace("_", " ")}</span>
            ) : (
              <>
                <span className="note" style={{ margin: 0 }}>asked {ago(a.asked_at)}</span>
                {(["referred", "declined", "no_reply"] as const).map((o) => (
                  <button key={o} disabled={pending}
                          onClick={() => post({ ask_id: a.id, outcome: o }).catch(() => setNote("Failed to save"))}>
                    {o === "no_reply" ? "No reply" : o[0].toUpperCase() + o.slice(1)}
                  </button>
                ))}
              </>
            )}
          </div>
        );
      })}
      {note && <div className="note" style={{ whiteSpace: "pre-wrap" }}>{note}</div>}
    </div>
  );
}
