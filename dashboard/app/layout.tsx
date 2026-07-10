import "./globals.css";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "JJ Strategy — Paper Trading Dashboard",
  description: "Trade log and performance dashboard for the JJ NY-session paper trading bot (TopStep/Tradovate).",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
