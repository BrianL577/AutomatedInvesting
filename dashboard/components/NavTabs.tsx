"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

export default function NavTabs({ tabs }: { tabs: { href: string; label: string }[] }) {
  const pathname = usePathname();

  return (
    <div className="navbar-links" role="tablist">
      {tabs.map((tab) => {
        const active = tab.href === "/" ? pathname === "/" : pathname.startsWith(tab.href);
        return (
          <Link
            key={tab.href}
            href={tab.href}
            role="tab"
            aria-selected={active}
            className={`navbar-tab${active ? " navbar-tab-active" : ""}`}
          >
            {tab.label}
          </Link>
        );
      })}
    </div>
  );
}
