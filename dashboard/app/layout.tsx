import "./globals.css";
import "./ui.css";
import type { Metadata, Viewport } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import { cookies } from "next/headers";

// Self-hosted at build time by next/font — no runtime request, no extra
// dependency, no layout shift. Inter for text; the mono is
// reserved for numbers, where tabular figures actually matter.
const sans = Inter({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-sans",
  display: "swap",
});
const mono = JetBrains_Mono({
  subsets: ["latin"],
  weight: ["400", "600", "700"],
  variable: "--font-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Job Agent",
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f7f8fa" },
    { media: "(prefers-color-scheme: dark)", color: "#0f1115" },
  ],
  width: "device-width",
  initialScale: 1,
};

export default async function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  // Theme is read server-side from a cookie so the correct palette is in the
  // first paint — no flash, and no localStorage.
  // "light" | "dark" pin the palette; "system" (or no cookie) leaves the
  // attribute off so prefers-color-scheme decides (spec 002 FR-013).
  const cookie = (await cookies()).get("theme")?.value;
  const theme = cookie === "light" || cookie === "dark" ? cookie : undefined;

  return (
    <html lang="en" data-theme={theme} className={`${sans.variable} ${mono.variable}`}>
      <body>{children}</body>
    </html>
  );
}
