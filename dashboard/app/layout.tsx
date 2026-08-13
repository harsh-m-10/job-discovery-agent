import "./globals.css";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Job Agent",
  robots: { index: false, follow: false },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
