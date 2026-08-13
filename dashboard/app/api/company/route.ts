import { NextResponse } from "next/server";
import { patch } from "@/lib/db";

export const dynamic = "force-dynamic";

/**
 * Re-enable a board that tripped the consecutive-failure cutoff, or disable a
 * noisy one. Re-enabling also clears the failure count — otherwise the next
 * single failure would immediately trip the threshold again.
 */
export async function POST(request: Request) {
  let body: any;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "invalid json" }, { status: 400 });
  }

  const id = Number(body?.id);
  if (!Number.isInteger(id) || id <= 0) {
    return NextResponse.json({ error: "id required" }, { status: 400 });
  }
  const active = Boolean(body?.active);

  const [saved] = await patch("companies", `id=eq.${id}`, {
    active,
    ...(active ? { consecutive_failures: 0 } : {}),
  });
  return NextResponse.json({ ok: true, company: saved });
}
