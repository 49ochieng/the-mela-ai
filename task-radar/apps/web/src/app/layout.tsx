import "./globals.css";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Mela Task Radar — Find the work hiding in your inbox.",
  description:
    "Mela Task Radar scans Outlook and Teams, extracts action items with AI, and syncs to Excel and Planner so nothing falls off your radar.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}

