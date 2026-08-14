import "./globals.css";
import type { Metadata, Viewport } from "next";
import { Space_Grotesk, JetBrains_Mono } from "next/font/google";
import { cookies } from "next/headers";

// Self-hosted at build time by next/font — no runtime request, no extra
// dependency, no layout shift. Grotesk carries the character; the mono is
// reserved for numbers, where tabular figures actually matter.
const sans = Space_Grotesk({
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
  themeColor: "#0b0d11",
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
  const theme = (await cookies()).get("theme")?.value === "light" ? "light" : "dark";

  return (
    <html lang="en" data-theme={theme} className={`${sans.variable} ${mono.variable}`}>
      <body>{children}</body>
    </html>
  );
}
