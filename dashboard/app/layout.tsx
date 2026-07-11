import "./globals.css";
import type { Metadata } from "next";
import NavBar from "../components/NavBar";

export const metadata: Metadata = {
  title: "JJ Strategy — Paper Trading Dashboard",
  description: "Trade log and performance dashboard for the JJ NY-session paper trading bot (TopStep/Tradovate).",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <NavBar />
        {children}
      </body>
    </html>
  );
}
