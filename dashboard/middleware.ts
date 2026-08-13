import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

/**
 * Access control (spec §9.1).
 *
 * Vercel's password protection is a paid feature, so the whole dashboard lives
 * under /d/<32-char secret> and everything else returns 404 — not 403, which
 * would confirm the path exists. The secret is compared in constant time so the
 * 404 cannot be turned into an oracle by timing.
 */

const SECRET = process.env.DASHBOARD_SECRET ?? "";

function constantTimeEquals(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  // API routes authenticate on the secret in a header instead of the path, so
  // it never lands in a server access log as part of the URL.
  if (pathname.startsWith("/api/")) {
    const provided = request.headers.get("x-dashboard-secret") ?? "";
    if (!SECRET || !constantTimeEquals(provided, SECRET)) {
      return new NextResponse("Not Found", { status: 404 });
    }
    return NextResponse.next();
  }

  const segments = pathname.split("/").filter(Boolean);
  if (segments[0] !== "d" || !SECRET || !constantTimeEquals(segments[1] ?? "", SECRET)) {
    return new NextResponse("Not Found", {
      status: 404,
      headers: { "X-Robots-Tag": "noindex, nofollow" },
    });
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
